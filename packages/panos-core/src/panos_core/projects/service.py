"""Project registry service.

Registering a project grants Panos tools a working scope, so registration refuses
locations that would make that scope dangerously broad: filesystem roots, the user's
home directory itself, system directories and Panos's own data directories.

Privacy-mode changes and policy overrides are explicit, audited user actions
(AC-US005-05, ADR-005).
"""

import os
import re
from collections.abc import Sequence
from pathlib import Path

from panos_core.audit import AuditService
from panos_core.config import PanosSettings
from panos_core.domain import PermissionLevel, PrivacyMode, Project
from panos_core.domain.errors import DomainError
from panos_core.persistence.unit_of_work import UnitOfWorkFactory
from panos_core.policy import load_default_policy

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._\-]{0,99}$")


class ProjectError(DomainError):
    """Registration/update refused. ``reason`` is a stable code for API/CLI mapping."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _forbidden_roots(settings: PanosSettings) -> list[Path]:
    roots = [
        Path.home(),
        settings.data_dir,
        settings.config_dir,
        settings.log_dir,
        settings.runtime_dir,
    ]
    if os.name == "nt":
        for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            value = os.environ.get(var)
            if value:
                roots.append(Path(value))
    else:
        roots += [
            Path(p)
            for p in (
                "/bin",
                "/boot",
                "/dev",
                "/etc",
                "/lib",
                "/proc",
                "/sbin",
                "/sys",
                "/usr",
                "/var",
                "/System",
                "/Library",
            )
        ]
    return [r.resolve() for r in roots if r.exists()]


class ProjectService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        audit: AuditService,
        settings: PanosSettings,
        node_id: str,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit = audit
        self._settings = settings
        self._node_id = node_id

    # ------------------------------------------------------------------ queries
    async def list(self) -> Sequence[Project]:
        async with self._uow_factory() as uow:
            return await uow.projects.list()

    async def get(self, ref: str) -> Project:
        """Look a project up by id or by (case-insensitive) name."""
        async with self._uow_factory() as uow:
            project = await uow.projects.get(ref) if ref.startswith("prj_") else None
            if project is None:
                project = await uow.projects.get_by_name(ref)
            if project is None:
                for candidate in await uow.projects.list():
                    if candidate.name.lower() == ref.lower():
                        return candidate
        if project is None:
            raise ProjectError("not_found", f"project {ref!r} not found")
        return project

    # ------------------------------------------------------------------ registration
    def validate_location(self, path: Path) -> Path:
        if not path.is_absolute():
            raise ProjectError("invalid_path", "project path must be absolute")
        try:
            resolved = path.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise ProjectError("not_found", f"path does not exist: {path}") from exc
        if not resolved.is_dir():
            raise ProjectError("invalid_path", "project path must be a directory")
        if not os.access(resolved, os.R_OK):
            raise ProjectError("not_accessible", "project directory is not readable")
        if resolved.parent == resolved:
            raise ProjectError("too_broad", "a filesystem root cannot be a project")
        for root in _forbidden_roots(self._settings):
            if resolved == root:
                raise ProjectError("too_broad", f"{resolved} is too broad to be a project")
            if root != Path.home().resolve() and root in resolved.parents:
                raise ProjectError("forbidden_location", f"projects cannot live inside {root}")
        return resolved

    async def register(
        self,
        path: Path,
        *,
        name: str | None = None,
        preferred_ide: str | None = None,
        actor: str = "user",
    ) -> Project:
        """Register an existing directory (US-011). New projects are LOCAL_ONLY (ADR-024)."""
        resolved = self.validate_location(path)
        project_name = name or resolved.name
        if not _NAME_RE.match(project_name):
            raise ProjectError("invalid_name", f"invalid project name {project_name!r}")
        async with self._uow_factory() as uow:
            if await uow.projects.get_by_path(str(resolved)) is not None:
                raise ProjectError("duplicate", f"{resolved} is already registered")
            for existing in await uow.projects.list():
                if existing.name.lower() == project_name.lower():
                    raise ProjectError("duplicate", f"a project named {project_name!r} exists")
            project = Project(
                name=project_name,
                path=str(resolved),
                node_id=self._node_id,
                preferred_ide=preferred_ide,
            )
            await uow.projects.add(project)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="project.registered",
            action="project.register",
            project_id=project.id,
            metadata={
                "path": project.path,
                "name": project.name,
                "privacy_mode": project.privacy_mode,
            },
        )
        return project

    async def create(self, name: str, *, actor: str = "user") -> Project:
        """Create a new empty project folder in the workspace (BR-029) and register it."""
        if not _NAME_RE.match(name) or name in (".", ".."):
            raise ProjectError("invalid_name", f"invalid project name {name!r}")
        workspace = self._settings.workspace_dir
        workspace.mkdir(parents=True, exist_ok=True)
        target = workspace / name
        if target.exists():
            raise ProjectError("duplicate", f"{target} already exists")
        target.mkdir()
        return await self.register(target, name=name, actor=actor)

    # ------------------------------------------------------------------ updates
    async def set_preferred_ide(self, ref: str, app_key: str | None, *, actor: str) -> Project:
        project = await self.get(ref)
        project.preferred_ide = app_key
        await self._save(project)
        await self._audit.record(
            actor=actor,
            event_type="project.updated",
            action="project.preferred_ide",
            project_id=project.id,
            metadata={"preferred_ide": app_key},
        )
        return project

    async def set_privacy_mode(self, ref: str, mode: PrivacyMode, *, actor: str) -> Project:
        """Explicit, audited change of the privacy boundary (AC-US005-05)."""
        project = await self.get(ref)
        previous = project.privacy_mode
        project.privacy_mode = mode
        await self._save(project)
        await self._audit.record(
            actor=actor,
            event_type="project.privacy_changed",
            action="project.privacy_mode",
            project_id=project.id,
            metadata={"from": previous, "to": mode},
        )
        return project

    async def set_policy_override(
        self, ref: str, action: str, level: PermissionLevel | None, *, actor: str
    ) -> Project:
        """Set (or clear with ``None``) a per-project permission override.

        Overrides that the policy engine would ignore (lowering P3/P4, or lowering a
        non-loosenable rule) are rejected here so the stored configuration is honest.
        """
        policy = load_default_policy()
        rule = policy.rules.get(action)
        if rule is None:
            raise ProjectError("unknown_action", f"unknown action class {action!r}")
        if (
            level is not None
            and level < rule.level
            and not (
                rule.loosenable
                and rule.level == PermissionLevel.P2_CONTROLLED
                and level == PermissionLevel.P1_SAFE_WRITE
            )
        ):
            raise ProjectError("cannot_loosen", f"{action} cannot be lowered to {level.name}")
        project = await self.get(ref)
        if level is None:
            project.policy_overrides.pop(action, None)
        else:
            project.policy_overrides[action] = level
        project.policy_overrides = dict(project.policy_overrides)  # trigger validation
        await self._save(project)
        await self._audit.record(
            actor=actor,
            event_type="project.policy_changed",
            action=action,
            project_id=project.id,
            metadata={
                "level": level.name if level is not None else None,
                "default": rule.level.name,
            },
        )
        return project

    async def _save(self, project: Project) -> None:
        async with self._uow_factory() as uow:
            await uow.projects.update(project)
            await uow.commit()
