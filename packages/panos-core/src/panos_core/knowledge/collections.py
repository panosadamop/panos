"""Knowledge collections outside projects (FR-019/020, BR-012, M8-04).

A collection is an indexed source of company or external knowledge: a local folder,
selected websites, or a Google Drive / OneDrive / SharePoint folder through a connector.
Collections share the knowledge index with projects (their id is the index scope) and are
LOCAL_ONLY unless the owner relaxes them.

Retrieval follows FR-020: the current project first, then company knowledge (folders and
cloud drives), then approved external sources (websites). Within a tier, hits are ordered
by relevance. Every hit keeps its provenance (URI, lines, hash, time, privacy label).
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx

from panos_core.ai.context import Scope
from panos_core.audit import AuditService
from panos_core.connectors.http import ConnectorHttpError
from panos_core.connectors.service import ConnectorError, ConnectorService
from panos_core.domain.clock import utc_now
from panos_core.domain.connectors import (
    CollectionKind,
    ConnectorKind,
    KnowledgeCollection,
)
from panos_core.domain.enums import PrivacyMode
from panos_core.domain.errors import DomainError
from panos_core.knowledge.index import IndexStats, SearchHit
from panos_core.knowledge.service import KnowledgeService
from panos_core.knowledge.sources import (
    Gathered,
    WebsiteSource,
    collect_folder,
    collect_google_drive,
    collect_onedrive,
    remote_id,
)
from panos_core.persistence.unit_of_work import UnitOfWorkFactory

# FR-020 retrieval tiers (lower first).
TIER_PROJECT: Final = 1
TIER_COMPANY: Final = 2
TIER_EXTERNAL: Final = 5
TIERS: Final = {
    CollectionKind.FOLDER: TIER_COMPANY,
    CollectionKind.GOOGLE_DRIVE: TIER_COMPANY,
    CollectionKind.ONEDRIVE: TIER_COMPANY,
    CollectionKind.WEBSITE: TIER_EXTERNAL,
}
PROVIDERS: Final = {
    CollectionKind.FOLDER: "filesystem",
    CollectionKind.WEBSITE: "website",
    CollectionKind.GOOGLE_DRIVE: "google_drive",
    CollectionKind.ONEDRIVE: "onedrive",
}


class CollectionError(DomainError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


_SCOPES: Final[dict[int, Scope]] = {
    TIER_PROJECT: "project",
    TIER_COMPANY: "company",
    TIER_EXTERNAL: "external",
}


@dataclass(frozen=True)
class RankedHit:
    hit: SearchHit
    tier: int
    source: str  # "project" or the collection name

    @property
    def scope(self) -> Scope:
        """The prompt-context scope, so context assembly keeps the FR-020 order."""
        return _SCOPES[self.tier]


@dataclass(frozen=True)
class CollectionIndexStats:
    stats: IndexStats
    skipped: list[str]


def _validate_location(kind: CollectionKind, location: dict[str, Any]) -> dict[str, Any]:
    try:
        if kind == CollectionKind.FOLDER:
            path = Path(str(location.get("path", ""))).expanduser()
            if not path.is_absolute() or not path.is_dir():
                raise ValueError(f"{path} is not an existing absolute folder")
            return {"path": str(path.resolve())}
        if kind == CollectionKind.WEBSITE:
            urls = [str(u).strip() for u in location.get("urls", []) if str(u).strip()]
            if not urls or len(urls) > 200:
                raise ValueError("give 1 to 200 page URLs")
            bad = [u for u in urls if not u.startswith(("https://", "http://"))]
            if bad:
                raise ValueError(f"only http(s) pages can be indexed: {bad[0]}")
            return {"urls": list(dict.fromkeys(urls))}
        if kind == CollectionKind.GOOGLE_DRIVE:
            return {"folder_id": remote_id(str(location.get("folder_id", "")), "folder id")}
        return {
            "drive_id": remote_id(str(location.get("drive_id", "")), "drive id"),
            "item_id": remote_id(str(location.get("item_id") or "root"), "item id"),
        }
    except ValueError as exc:
        raise CollectionError("invalid", str(exc)) from exc


class CollectionService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        knowledge: KnowledgeService,
        connectors: ConnectorService,
        audit: AuditService,
        *,
        web_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._knowledge = knowledge
        self._connectors = connectors
        self._audit = audit
        self._web_transport = web_transport
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ registry
    async def create(
        self,
        name: str,
        kind: CollectionKind,
        location: dict[str, Any],
        *,
        connector: str | None = None,
        privacy: PrivacyMode = PrivacyMode.LOCAL_ONLY,
        actor: str = "user",
    ) -> KnowledgeCollection:
        clean = _validate_location(kind, location)
        connector_id = None
        if kind in (CollectionKind.GOOGLE_DRIVE, CollectionKind.ONEDRIVE):
            need = (
                ConnectorKind.GOOGLE
                if kind == CollectionKind.GOOGLE_DRIVE
                else ConnectorKind.MICROSOFT
            )
            try:
                account = (
                    await self._connectors.get(connector)
                    if connector
                    else await self._connectors.first(
                        need, "drive" if need == ConnectorKind.GOOGLE else None
                    )
                )
            except ConnectorError as exc:
                raise CollectionError(exc.reason, str(exc)) from exc
            if account.kind != need or (
                need == ConnectorKind.GOOGLE and "drive" not in account.scopes
            ):
                raise CollectionError(
                    "invalid",
                    f"{account.label} cannot read {kind.value}"
                    + (
                        " (connect Google with --feature drive)"
                        if need == ConnectorKind.GOOGLE
                        else ""
                    ),
                )
            connector_id = account.id
        try:
            collection = KnowledgeCollection(
                name=name,
                kind=kind,
                location=clean,
                connector_id=connector_id,
                privacy_mode=privacy,
            )
        except ValueError as exc:
            raise CollectionError("invalid", f"invalid collection name {name!r}") from exc
        async with self._uow_factory() as uow:
            if await uow.connectors.get_collection(name) is not None:
                raise CollectionError("duplicate", f"a collection named {name!r} exists")
            await uow.connectors.save_collection(collection)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="knowledge.collection_added",
            action="knowledge.collection",
            metadata={
                "collection": collection.id,
                "name": name,
                "kind": kind.value,
                "privacy": privacy.value,
            },
        )
        return collection

    async def all(self) -> list[KnowledgeCollection]:
        async with self._uow_factory() as uow:
            return list(await uow.connectors.collections())

    async def get(self, ref: str) -> KnowledgeCollection:
        async with self._uow_factory() as uow:
            found = await uow.connectors.get_collection(ref)
        if found is None:
            raise CollectionError("not_found", f"no knowledge collection {ref!r}")
        return found

    async def update(
        self,
        ref: str,
        *,
        privacy: PrivacyMode | None = None,
        enabled: bool | None = None,
        actor: str = "user",
    ) -> KnowledgeCollection:
        collection = await self.get(ref)
        if privacy is not None:
            collection.privacy_mode = privacy
        if enabled is not None:
            collection.enabled = enabled
        async with self._uow_factory() as uow:
            await uow.connectors.save_collection(collection)
            await uow.commit()
        if privacy is not None:  # the index carries the label on every source
            await asyncio.to_thread(
                self._knowledge.index.relabel, collection.id, collection.privacy_mode.value
            )
        await self._audit.record(
            actor=actor,
            event_type="knowledge.collection_changed",
            action="knowledge.collection",
            metadata={
                "collection": collection.id,
                "privacy": collection.privacy_mode.value,
                "enabled": collection.enabled,
            },
        )
        return collection

    async def delete(self, ref: str, *, actor: str = "user") -> None:
        collection = await self.get(ref)
        await asyncio.to_thread(self._knowledge.index.remove_project, collection.id)
        async with self._uow_factory() as uow:
            await uow.connectors.delete_collection(collection.id)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="knowledge.collection_removed",
            action="knowledge.collection",
            metadata={"collection": collection.id, "name": collection.name},
        )

    # ------------------------------------------------------------------ indexing
    async def index(self, ref: str, *, actor: str = "user") -> CollectionIndexStats:
        collection = await self.get(ref)
        if not collection.enabled:
            raise CollectionError("invalid_state", f"collection {collection.name} is disabled")
        lock = self._locks.setdefault(collection.id, asyncio.Lock())
        async with lock:
            index = self._knowledge.index
            known = await asyncio.to_thread(index.known_hashes, collection.id)
            try:
                gathered = await self._gather(collection, known)
            except (ConnectorHttpError, ConnectorError) as exc:
                raise CollectionError("upstream", f"{collection.name}: {exc}") from exc
            indexed, removed, chunks = await asyncio.to_thread(
                index.update,
                collection.id,
                collection.privacy_mode.value,
                gathered.changed,
                gathered.seen,
                provider=PROVIDERS[collection.kind],
            )
            collection.last_indexed_at = utc_now()
            async with self._uow_factory() as uow:
                await uow.connectors.save_collection(collection)
                await uow.commit()
        total = len(gathered.seen)
        stats = IndexStats(total, indexed, total - indexed, removed, chunks)
        await self._audit.record(
            actor=actor,
            event_type="knowledge.indexed",
            action="knowledge.index",
            metadata={
                "collection": collection.id,
                **stats.__dict__,
                "skipped": len(gathered.skipped),
            },
        )
        return CollectionIndexStats(stats, gathered.skipped[:50])

    async def _gather(self, c: KnowledgeCollection, known: dict[str, str]) -> Gathered:
        if c.kind == CollectionKind.FOLDER:
            root = Path(c.location["path"])
            if not root.is_dir():
                raise CollectionError("not_found", f"folder {root} no longer exists")
            return await asyncio.to_thread(collect_folder, root, known)
        if c.kind == CollectionKind.WEBSITE:
            return await WebsiteSource(self._web_transport).collect(c.location["urls"], known)
        account = await self._connectors.get(c.connector_id or "")
        http = self._connectors.client(account)
        try:
            if c.kind == CollectionKind.GOOGLE_DRIVE:
                gathered = await collect_google_drive(http, c.location["folder_id"], known)
            else:
                gathered = await collect_onedrive(
                    http, c.location["drive_id"], c.location["item_id"], known
                )
        finally:
            await http.aclose()
        await self._connectors.touch(account)
        return gathered

    # ------------------------------------------------------------------ retrieval (FR-020)
    async def search(
        self,
        query: str,
        *,
        project_id: str | None = None,
        collections: list[str] | None = None,
        limit: int = 20,
        privacy: PrivacyMode = PrivacyMode.LOCAL_ONLY,
    ) -> list[RankedHit]:
        """Ranked search: current project, then company knowledge, then external sources.

        ``collections=None`` means every enabled collection; ``[]`` means none.
        """
        scopes: list[tuple[str, int, str]] = []
        if project_id is not None:
            scopes.append((project_id, TIER_PROJECT, "project"))
        for c in await self.all():
            if c.enabled and (collections is None or c.id in collections or c.name in collections):
                scopes.append((c.id, TIERS[c.kind], c.name))
        ranked: list[RankedHit] = []
        for scope, tier, source in scopes:
            if tier == TIER_PROJECT:
                hits = await self._knowledge.search(
                    query, project_id=scope, limit=limit, privacy=privacy
                )
            else:
                hits = await asyncio.to_thread(
                    self._knowledge.index.search, query, project_id=scope, limit=limit
                )
            ranked += [RankedHit(h, tier, source) for h in hits]
        ranked.sort(key=lambda r: (r.tier, -r.hit.score))
        return ranked[:limit]
