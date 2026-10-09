"""Application registry service (FR-003, US-010).

* ``refresh`` discovers catalogued applications through the OS adapter and persists them.
* ``resolve`` maps a key, alias or name to an available application.
* ``preferred_for`` picks the IDE for a project: the project's own choice → the user's
  per-technology preference → a catalog default matching the detected stack → VS Code.
"""

import asyncio
from collections.abc import Sequence

from panos_core.apps.catalog import CATALOG, find_entry
from panos_core.audit import AuditService
from panos_core.domain import Application, Project, RepositorySnapshot
from panos_core.domain.clock import utc_now
from panos_core.domain.errors import DomainError
from panos_core.persistence.unit_of_work import UnitOfWorkFactory
from panos_core.platform.desktop import DesktopAdapter

PREFERENCE_PREFIX = "ide.preferred."


class AppError(DomainError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class AppService:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, audit: AuditService, desktop: DesktopAdapter
    ) -> None:
        self._uow_factory = uow_factory
        self._audit = audit
        self.desktop = desktop
        # Startup discovery and explicit refreshes can overlap; upserts must not race.
        self._refresh_lock = asyncio.Lock()

    async def refresh(self, *, actor: str = "system") -> Sequence[Application]:
        async with self._refresh_lock:
            return await self._refresh(actor)

    async def _refresh(self, actor: str) -> Sequence[Application]:
        # Discovery reads the registry/filesystem: keep it off the event loop.
        discovered = await asyncio.to_thread(self.desktop.discover_apps, CATALOG)
        async with self._uow_factory() as uow:
            for hit in discovered:
                entry = find_entry(hit.key)
                if entry is None:
                    continue
                await uow.applications.upsert(
                    Application(
                        key=entry.key,
                        name=entry.name,
                        kind=entry.kind,
                        executable=hit.executable,
                        version=hit.version,
                        source=hit.source,
                        aliases=list(entry.aliases),
                        available=True,
                        discovered_at=utc_now(),
                    )
                )
            await uow.applications.mark_unavailable_except({h.key for h in discovered})
            await uow.commit()
            apps = await uow.applications.list()
        await self._audit.record(
            actor=actor,
            event_type="apps.discovered",
            action="app.discover",
            metadata={"count": len(apps), "keys": [a.key for a in apps]},
        )
        return apps

    async def list(self) -> Sequence[Application]:
        async with self._uow_factory() as uow:
            return await uow.applications.list()

    async def resolve(self, ref: str) -> Application:
        entry = find_entry(ref)
        if entry is None:
            raise AppError("unsupported", f"{ref!r} is not a supported application")
        async with self._uow_factory() as uow:
            app = await uow.applications.get_by_key(entry.key)
        if app is None or not app.available:
            raise AppError("not_installed", f"{entry.name} was not found on this computer")
        return app

    async def set_technology_preference(self, technology: str, app_key: str) -> None:
        app = await self.resolve(app_key)
        async with self._uow_factory() as uow:
            await uow.preferences.set(PREFERENCE_PREFIX + technology.lower(), app.key)
            await uow.commit()

    async def preferred_for(
        self, project: Project, snapshot: RepositorySnapshot | None
    ) -> Application:
        if project.preferred_ide:
            return await self.resolve(project.preferred_ide)
        technologies: list[str] = []
        if snapshot is not None:
            stack = snapshot.stack
            technologies += list(stack.get("frameworks", []))
            if stack.get("primary_language"):
                technologies.append(stack["primary_language"])
            technologies += list(stack.get("build_systems", []))
        async with self._uow_factory() as uow:
            for tech in technologies:
                pref = await uow.preferences.get(PREFERENCE_PREFIX + tech.lower())
                if pref is not None and isinstance(pref.value, str):
                    app = await uow.applications.get_by_key(pref.value)
                    if app is not None and app.available:
                        return app
            for tech in technologies:
                for entry in CATALOG:
                    if entry.kind == "ide" and tech in entry.technologies:
                        app = await uow.applications.get_by_key(entry.key)
                        if app is not None and app.available:
                            return app
            for key in ("vscode", "sublime"):
                app = await uow.applications.get_by_key(key)
                if app is not None and app.available:
                    return app
        raise AppError("no_ide", "no supported IDE or editor is installed")
