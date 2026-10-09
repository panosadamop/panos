"""Applications: discovery, launch and per-technology IDE preferences (FR-003, US-010)."""

from fastapi import APIRouter

from panos_core.apps.service import AppError
from panos_core.domain import Application
from panos_core.projects import ProjectError
from panos_node_api.routers.deps import LOCAL_USER, Container, raise_for_reason, tool_result_view
from panos_node_api.schemas import (
    AppLaunchRequest,
    AppList,
    AppView,
    IdePreferenceRequest,
    ToolResultView,
)

router = APIRouter(tags=["apps"])


def _view(a: Application) -> AppView:
    return AppView(
        key=a.key,
        name=a.name,
        kind=a.kind,
        executable=a.executable,
        version=a.version,
        source=a.source,
        aliases=a.aliases,
    )


@router.get("/apps", response_model=AppList)
async def list_apps(container: Container) -> AppList:
    return AppList(apps=[_view(a) for a in await container.apps.list()])


@router.post("/apps/refresh", response_model=AppList)
async def refresh_apps(container: Container) -> AppList:
    return AppList(apps=[_view(a) for a in await container.apps.refresh(actor=LOCAL_USER)])


@router.post("/apps/{ref}/launch", response_model=ToolResultView)
async def launch_app(ref: str, body: AppLaunchRequest, container: Container) -> ToolResultView:
    project = None
    if body.project:
        try:
            project = await container.projects.get(body.project)
        except ProjectError as exc:
            raise_for_reason(exc.reason, str(exc))
    ctx = await container.context_for(project, actor=LOCAL_USER)
    raw = {"app": ref, "target": body.target, "path": body.path, "url": body.url}
    return tool_result_view(await container.tools.invoke("app.launch", raw, ctx))


@router.put("/preferences/ide/{technology}", response_model=AppView)
async def set_ide_preference(
    technology: str, body: IdePreferenceRequest, container: Container
) -> AppView:
    try:
        await container.apps.set_technology_preference(technology, body.app)
        return _view(await container.apps.resolve(body.app))
    except AppError as exc:
        raise_for_reason(exc.reason, str(exc))
