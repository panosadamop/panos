"""Projects: registry, analysis, knowledge, opening in an IDE (FR-004/005, US-010/011/012)."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from panos_core.apps.service import AppError
from panos_core.domain import PrivacyMode, Project, RepositorySnapshot
from panos_core.policy import parse_level
from panos_core.projects import ProjectError
from panos_node_api.routers.deps import LOCAL_USER, Container, raise_for_reason, tool_result_view
from panos_node_api.schemas import (
    IndexStatsView,
    OpenPreview,
    PolicyOverrideRequest,
    ProjectCreateRequest,
    ProjectList,
    ProjectOpenRequest,
    ProjectUpdateRequest,
    ProjectView,
    SearchHitView,
    SearchResults,
    SnapshotView,
    ToolResultView,
)

router = APIRouter(tags=["projects"])


def _view(p: Project) -> ProjectView:
    return ProjectView(
        id=p.id,
        name=p.name,
        path=p.path,
        privacy_mode=p.privacy_mode,
        preferred_ide=p.preferred_ide,
        status=p.status,
        created_at=p.created_at,
        policy_overrides={k: v.name for k, v in p.policy_overrides.items()},
        knowledge_enabled=p.knowledge_enabled,
    )


def _snapshot_view(s: RepositorySnapshot) -> SnapshotView:
    return SnapshotView(
        id=s.id,
        project_id=s.project_id,
        commit_hash=s.commit_hash,
        stack=s.stack,
        commands=s.commands,
        structure=s.structure,
        clarifications=s.clarifications,
        confidence=s.confidence,
        needs_clarification=s.needs_clarification,
        indexed_at=s.indexed_at,
    )


async def _project(container: Container, ref: str) -> Project:
    try:
        return await container.projects.get(ref)
    except ProjectError as exc:
        raise_for_reason(exc.reason, str(exc))


@router.get("/projects", response_model=ProjectList)
async def list_projects(container: Container) -> ProjectList:
    return ProjectList(projects=[_view(p) for p in await container.projects.list()])


@router.post("/projects", response_model=ProjectView, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreateRequest, container: Container) -> ProjectView:
    try:
        if body.create:
            if not body.name:
                raise HTTPException(status_code=422, detail="name is required to create")
            project = await container.projects.create(body.name, actor=LOCAL_USER)
        else:
            if not body.path:
                raise HTTPException(status_code=422, detail="path is required to register")
            project = await container.projects.register(
                Path(body.path),
                name=body.name,
                preferred_ide=body.preferred_ide,
                actor=LOCAL_USER,
            )
    except ProjectError as exc:
        raise_for_reason(exc.reason, str(exc))
    return _view(project)


@router.get("/projects/{ref}", response_model=ProjectView)
async def get_project(ref: str, container: Container) -> ProjectView:
    return _view(await _project(container, ref))


@router.patch("/projects/{ref}", response_model=ProjectView)
async def update_project(ref: str, body: ProjectUpdateRequest, container: Container) -> ProjectView:
    project = await _project(container, ref)
    try:
        if body.preferred_ide is not None:
            ide = (
                None
                if body.preferred_ide == ""
                else (await container.apps.resolve(body.preferred_ide)).key
            )
            project = await container.projects.set_preferred_ide(project.id, ide, actor=LOCAL_USER)
        if body.privacy_mode is not None and body.privacy_mode != project.privacy_mode:
            if not body.confirm_privacy_change:
                raise HTTPException(
                    status_code=409,
                    detail="changing the privacy mode requires confirm_privacy_change=true",
                )
            project = await container.projects.set_privacy_mode(
                project.id, PrivacyMode(body.privacy_mode), actor=LOCAL_USER
            )
    except (ProjectError, AppError) as exc:
        raise_for_reason(exc.reason, str(exc))
    return _view(project)


@router.put("/projects/{ref}/policy/{action}", response_model=ProjectView)
async def set_policy(
    ref: str, action: str, body: PolicyOverrideRequest, container: Container
) -> ProjectView:
    project = await _project(container, ref)
    try:
        level = parse_level(body.level) if body.level is not None else None
        project = await container.projects.set_policy_override(
            project.id, action, level, actor=LOCAL_USER
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProjectError as exc:
        raise_for_reason(exc.reason, str(exc))
    return _view(project)


@router.post("/projects/{ref}/analyze", response_model=SnapshotView)
async def analyze_project(ref: str, container: Container) -> SnapshotView:
    project = await _project(container, ref)
    try:
        snapshot = await container.analysis.analyze(project, actor=LOCAL_USER)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _snapshot_view(snapshot)


@router.get("/projects/{ref}/snapshot", response_model=SnapshotView)
async def latest_snapshot(ref: str, container: Container) -> SnapshotView:
    project = await _project(container, ref)
    snapshot = await container.analysis.latest(project.id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="project has not been analyzed yet")
    return _snapshot_view(snapshot)


@router.post("/projects/{ref}/index", response_model=IndexStatsView)
async def index_project(
    ref: str,
    container: Container,
    semantic: Annotated[bool, Query(description="also compute embeddings (local model)")] = False,
) -> IndexStatsView:
    project = await _project(container, ref)
    stats = await container.knowledge.index_project(project, actor=LOCAL_USER, semantic=semantic)
    return IndexStatsView(**stats.__dict__)


@router.get("/projects/{ref}/search", response_model=SearchResults)
async def search_project(
    ref: str,
    container: Container,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SearchResults:
    project = await _project(container, ref)
    hits = await container.knowledge.search(q, project_id=project.id, limit=limit)
    return SearchResults(hits=[SearchHitView(**h.__dict__) for h in hits])


@router.post("/projects/{ref}/open", response_model=ToolResultView | OpenPreview)
async def open_project(
    ref: str, body: ProjectOpenRequest, container: Container
) -> ToolResultView | OpenPreview:
    project = await _project(container, ref)
    if body.dry_run:
        try:
            app = await (
                container.apps.resolve(body.app)
                if body.app
                else container.apps.preferred_for(
                    project, await container.analysis.latest(project.id)
                )
            )
        except AppError as exc:
            raise_for_reason(exc.reason, str(exc))
        return OpenPreview(
            app=app.key, name=app.name, executable=app.executable, arguments=[project.path]
        )
    ctx = await container.context_for(project, actor=LOCAL_USER)
    result = await container.tools.invoke("project.open", {"app": body.app}, ctx)
    return tool_result_view(result)
