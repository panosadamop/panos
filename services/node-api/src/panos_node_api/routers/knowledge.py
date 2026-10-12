"""Knowledge collections and ranked search (FR-019/020, BR-012, M8-04)."""

from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from panos_core.domain.connectors import CollectionKind, KnowledgeCollection
from panos_core.domain.enums import PrivacyMode
from panos_core.knowledge.collections import CollectionError
from panos_core.projects.service import ProjectError
from panos_node_api.routers.deps import LOCAL_USER, Container, raise_for_reason

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class CollectionView(BaseModel):
    id: str
    name: str
    kind: str
    location: dict[str, Any]
    connector_id: str | None
    privacy_mode: str
    enabled: bool
    created_at: str
    last_indexed_at: str | None


def _view(c: KnowledgeCollection) -> CollectionView:
    return CollectionView(
        id=c.id,
        name=c.name,
        kind=c.kind.value,
        location=c.location,
        connector_id=c.connector_id,
        privacy_mode=c.privacy_mode.value,
        enabled=c.enabled,
        created_at=c.created_at.isoformat(),
        last_indexed_at=c.last_indexed_at.isoformat() if c.last_indexed_at else None,
    )


class CollectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    kind: CollectionKind
    location: dict[str, Any] = Field(
        description="folder: {path}; website: {urls}; google_drive: {folder_id}; "
        "onedrive: {drive_id, item_id?}"
    )
    connector: str | None = Field(default=None, description="account id (drives)")
    privacy_mode: PrivacyMode = PrivacyMode.LOCAL_ONLY


class CollectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    privacy_mode: PrivacyMode | None = None
    enabled: bool | None = None


class IndexView(BaseModel):
    files_seen: int
    indexed: int
    unchanged: int
    removed: int
    chunks: int
    skipped: list[str]


class RankedHitView(BaseModel):
    source: str
    tier: int
    scope_id: str
    uri: str
    start_line: int
    end_line: int
    snippet: str
    score: float
    privacy_mode: str
    content_hash: str
    indexed_at: str


@router.get("/collections", response_model=list[CollectionView])
async def list_collections(container: Container) -> Any:
    return [_view(c) for c in await container.collections.all()]


@router.post("/collections", response_model=CollectionView, status_code=status.HTTP_201_CREATED)
async def add_collection(body: CollectionIn, container: Container) -> Any:
    try:
        created = await container.collections.create(
            body.name,
            body.kind,
            body.location,
            connector=body.connector,
            privacy=body.privacy_mode,
            actor=LOCAL_USER,
        )
    except CollectionError as exc:
        raise_for_reason(exc.reason, str(exc))
    return _view(created)


@router.patch("/collections/{ref}", response_model=CollectionView)
async def update_collection(ref: str, body: CollectionPatch, container: Container) -> Any:
    try:
        updated = await container.collections.update(
            ref, privacy=body.privacy_mode, enabled=body.enabled, actor=LOCAL_USER
        )
    except CollectionError as exc:
        raise_for_reason(exc.reason, str(exc))
    return _view(updated)


@router.delete("/collections/{ref}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_collection(ref: str, container: Container) -> None:
    try:
        await container.collections.delete(ref, actor=LOCAL_USER)
    except CollectionError as exc:
        raise_for_reason(exc.reason, str(exc))


@router.post("/collections/{ref}/index", response_model=IndexView)
async def index_collection(ref: str, container: Container) -> Any:
    try:
        result = await container.collections.index(ref, actor=LOCAL_USER)
    except CollectionError as exc:
        raise_for_reason(exc.reason, str(exc))
    return IndexView(**result.stats.__dict__, skipped=result.skipped)


@router.get("/search", response_model=list[RankedHitView])
async def search(
    container: Container,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    project: str | None = None,
    collection: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Any:
    """FR-020 ranking: the project first, then company knowledge, then websites."""
    project_id, privacy = None, PrivacyMode.LOCAL_ONLY
    if project:
        try:
            found = await container.projects.get(project)
        except ProjectError as exc:
            raise_for_reason(exc.reason, str(exc))
        project_id, privacy = found.id, found.privacy_mode
    hits = await container.collections.search(
        q, project_id=project_id, collections=collection, limit=limit, privacy=privacy
    )
    return [
        RankedHitView(
            source=r.source,
            tier=r.tier,
            scope_id=r.hit.project_id,
            uri=r.hit.uri,
            start_line=r.hit.start_line,
            end_line=r.hit.end_line,
            snippet=r.hit.snippet,
            score=r.hit.score,
            privacy_mode=r.hit.privacy_mode,
            content_hash=r.hit.content_hash,
            indexed_at=r.hit.indexed_at,
        )
        for r in hits
    ]
