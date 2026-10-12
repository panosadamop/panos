"""Application, window and knowledge tools (FR-003, US-010, FR-020).

* Only catalogued, discovered applications can be launched ("approved application", P1).
* Launch arguments are restricted to the active project directory, a path inside it,
  or an http(s) URL for browsers: no free-form arguments reach the executable.
* ``os.open_path`` never opens executables with their default handler.
"""

from pathlib import Path
from typing import ClassVar, Literal, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from panos_core.apps.service import AppError, AppService
from panos_core.domain import RepositorySnapshot
from panos_core.knowledge.collections import CollectionService
from panos_core.knowledge.index import SearchHit
from panos_core.knowledge.service import KnowledgeService
from panos_core.platform.desktop import EXECUTABLE_EXTENSIONS, UnsupportedError
from panos_core.tools.contract import (
    ActionPlan,
    PlanRejectedError,
    SideEffect,
    Tool,
    ToolContext,
    ToolExecutionError,
    ToolSpec,
)
from panos_core.tools.paths import PathPolicyError, relative_to_root, resolve_in_root


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SnapshotSource(Protocol):
    """Port for the latest repository snapshot (implemented by AnalysisService)."""

    async def latest(self, project_id: str) -> RepositorySnapshot | None: ...


# ===================================================================== app.list
class AppListInput(_Model):
    refresh: bool = False


class AppOut(_Model):
    key: str
    name: str
    kind: str
    executable: str
    version: str | None
    aliases: list[str]


class AppListOutput(_Model):
    apps: list[AppOut]


class AppListTool(Tool[AppListInput, AppListOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="app.list",
        version="1",
        description="List supported installed applications.",
        input_model=AppListInput,
        output_model=AppListOutput,
        capabilities=frozenset({"app.discover"}),
        side_effect=SideEffect.NONE,
        timeout_s=60,
        cancellable=False,
        idempotent=True,
        privacy="Lists installed application names/paths.",
    )

    def __init__(self, apps: AppService) -> None:
        self._apps = apps

    async def plan(self, inp: AppListInput, ctx: ToolContext) -> ActionPlan:
        return ActionPlan(
            action="app.discover",
            target="local",
            summary="List applications",
            payload={"refresh": inp.refresh},
        )

    async def _execute(
        self, inp: AppListInput, ctx: ToolContext, plan: ActionPlan
    ) -> AppListOutput:
        apps = await (self._apps.refresh(actor=ctx.actor) if inp.refresh else self._apps.list())
        return AppListOutput(
            apps=[
                AppOut(
                    key=a.key,
                    name=a.name,
                    kind=a.kind,
                    executable=a.executable,
                    version=a.version,
                    aliases=a.aliases,
                )
                for a in apps
            ]
        )


# ===================================================================== app.launch
class AppLaunchInput(_Model):
    app: str = Field(description="application key, alias or name")
    target: Literal["none", "project", "path", "url"] = "none"
    path: str | None = None  # for target=path (inside the active project)
    url: str | None = None  # for target=url (browsers only)


class AppLaunchOutput(_Model):
    app: str
    pid: int
    arguments: list[str]


async def _launch_plan(
    apps: AppService, inp: AppLaunchInput, ctx: ToolContext, *, summary_prefix: str = "Open"
) -> ActionPlan:
    try:
        app = await apps.resolve(inp.app)
    except AppError as exc:
        raise PlanRejectedError(str(exc)) from exc
    args: list[str] = []
    if inp.target in ("project", "path"):
        if ctx.project is None:
            raise PlanRejectedError("no active project")
        root = Path(ctx.project.path).resolve()
        if inp.target == "project":
            args = [str(root)]
        else:
            try:
                args = [str(resolve_in_root(root, inp.path or ""))]
            except PathPolicyError as exc:
                raise PlanRejectedError(str(exc)) from exc
    elif inp.target == "url":
        if app.kind != "browser":
            raise PlanRejectedError("URLs can only be opened in a browser")
        parsed = urlparse(inp.url or "")
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise PlanRejectedError("only http(s) URLs can be opened")
        args = [inp.url or ""]
    shown = args[0] if args else ""
    return ActionPlan(
        action="app.launch",
        target=app.key,
        summary=f"{summary_prefix} {app.name} {shown}".strip(),
        payload={"app": app.key, "executable": app.executable, "args": args},
    )


class AppLaunchTool(Tool[AppLaunchInput, AppLaunchOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="app.launch",
        version="1",
        description="Launch a supported application, optionally with the project or a URL.",
        input_model=AppLaunchInput,
        output_model=AppLaunchOutput,
        capabilities=frozenset({"app.launch"}),
        side_effect=SideEffect.LOCAL,
        timeout_s=30,
        cancellable=False,
        idempotent=False,
        privacy="Opens project paths in local apps.",
    )

    def __init__(self, apps: AppService) -> None:
        self._apps = apps

    async def plan(self, inp: AppLaunchInput, ctx: ToolContext) -> ActionPlan:
        return await _launch_plan(self._apps, inp, ctx)

    async def _execute(
        self, inp: AppLaunchInput, ctx: ToolContext, plan: ActionPlan
    ) -> AppLaunchOutput:
        args = list(plan.payload["args"])
        try:
            pid = self._apps.desktop.launch(plan.payload["executable"], args)
        except OSError as exc:
            raise ToolExecutionError(f"launch failed: {exc}") from exc
        return AppLaunchOutput(app=plan.payload["app"], pid=pid, arguments=args)


# ===================================================================== project.open
class ProjectOpenInput(_Model):
    app: str | None = None  # default: the project's preferred IDE


class ProjectOpenTool(Tool[ProjectOpenInput, AppLaunchOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="project.open",
        version="1",
        description="Open the active project in its preferred (or a given) IDE.",
        input_model=ProjectOpenInput,
        output_model=AppLaunchOutput,
        capabilities=frozenset({"app.launch"}),
        side_effect=SideEffect.LOCAL,
        timeout_s=30,
        cancellable=False,
        idempotent=False,
        privacy="Opens the project in a local IDE.",
    )

    def __init__(self, apps: AppService, snapshots: SnapshotSource) -> None:
        self._apps = apps
        self._snapshots = snapshots

    async def plan(self, inp: ProjectOpenInput, ctx: ToolContext) -> ActionPlan:
        if ctx.project is None:
            raise PlanRejectedError("no active project")
        if inp.app is None:
            try:
                app = await self._apps.preferred_for(
                    ctx.project, await self._snapshots.latest(ctx.project.id)
                )
            except AppError as exc:
                raise PlanRejectedError(str(exc)) from exc
            ref = app.key
        else:
            ref = inp.app
        return await _launch_plan(self._apps, AppLaunchInput(app=ref, target="project"), ctx)

    async def _execute(
        self, inp: ProjectOpenInput, ctx: ToolContext, plan: ActionPlan
    ) -> AppLaunchOutput:
        args = list(plan.payload["args"])
        try:
            pid = self._apps.desktop.launch(plan.payload["executable"], args)
        except OSError as exc:
            raise ToolExecutionError(f"launch failed: {exc}") from exc
        return AppLaunchOutput(app=plan.payload["app"], pid=pid, arguments=args)


# ===================================================================== os.open_path
class OpenPathInput(_Model):
    path: str = "."


class OpenPathOutput(_Model):
    path: str


class OpenPathTool(Tool[OpenPathInput, OpenPathOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="os.open_path",
        version="1",
        description="Open a project file or folder with the OS default handler "
        "(never executables).",
        input_model=OpenPathInput,
        output_model=OpenPathOutput,
        capabilities=frozenset({"os.open_path"}),
        side_effect=SideEffect.LOCAL,
        timeout_s=30,
        cancellable=False,
        idempotent=True,
        privacy="Opens project files in local apps.",
    )

    def __init__(self, apps: AppService) -> None:
        self._apps = apps

    async def plan(self, inp: OpenPathInput, ctx: ToolContext) -> ActionPlan:
        if ctx.project is None:
            raise PlanRejectedError("no active project")
        root = Path(ctx.project.path).resolve()
        try:
            path = resolve_in_root(root, inp.path)
        except PathPolicyError as exc:
            raise PlanRejectedError(str(exc)) from exc
        if not path.exists():
            raise PlanRejectedError("path does not exist")
        if path.is_file() and path.suffix.lower() in EXECUTABLE_EXTENSIONS:
            raise PlanRejectedError("refusing to open an executable with its default handler")
        rel = relative_to_root(root, path)
        return ActionPlan(
            action="os.open_path", target=str(root), summary=f"Open {rel}", payload={"path": rel}
        )

    async def _execute(
        self, inp: OpenPathInput, ctx: ToolContext, plan: ActionPlan
    ) -> OpenPathOutput:
        assert ctx.project is not None  # noqa: S101 - checked in plan
        root = Path(ctx.project.path).resolve()
        path = resolve_in_root(root, plan.payload["path"])
        try:
            self._apps.desktop.open_path(path)
        except (OSError, UnsupportedError) as exc:
            raise ToolExecutionError(str(exc)) from exc
        return OpenPathOutput(path=plan.payload["path"])


# ===================================================================== window.active / app.focus
class WindowActiveInput(_Model):
    pass


class WindowActiveOutput(_Model):
    title: str
    pid: int
    process_name: str | None
    executable: str | None
    bounds: list[int] | None


class WindowActiveTool(Tool[WindowActiveInput, WindowActiveOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="window.active",
        version="1",
        description="Metadata of the active window (no pixels; screenshots are separate).",
        input_model=WindowActiveInput,
        output_model=WindowActiveOutput,
        capabilities=frozenset({"window.read"}),
        side_effect=SideEffect.NONE,
        timeout_s=10,
        cancellable=False,
        idempotent=True,
        privacy="Window titles can reveal document names; treated as local data.",
    )

    def __init__(self, apps: AppService) -> None:
        self._apps = apps

    async def plan(self, inp: WindowActiveInput, ctx: ToolContext) -> ActionPlan:
        return ActionPlan(
            action="window.read", target="local", summary="Read active window", payload={}
        )

    async def _execute(
        self, inp: WindowActiveInput, ctx: ToolContext, plan: ActionPlan
    ) -> WindowActiveOutput:
        try:
            info = self._apps.desktop.active_window()
        except UnsupportedError as exc:
            raise ToolExecutionError(str(exc)) from exc
        return WindowActiveOutput(
            title=info.title,
            pid=info.pid,
            process_name=info.process_name,
            executable=info.executable,
            bounds=list(info.bounds) if info.bounds else None,
        )


class AppFocusInput(_Model):
    pid: int = Field(gt=0)


class AppFocusOutput(_Model):
    focused: bool


class AppFocusTool(Tool[AppFocusInput, AppFocusOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="app.focus",
        version="1",
        description="Bring a process's main window to the front.",
        input_model=AppFocusInput,
        output_model=AppFocusOutput,
        capabilities=frozenset({"app.focus"}),
        side_effect=SideEffect.LOCAL,
        timeout_s=10,
        cancellable=False,
        idempotent=True,
        privacy="Changes the focused window.",
    )

    def __init__(self, apps: AppService) -> None:
        self._apps = apps

    async def plan(self, inp: AppFocusInput, ctx: ToolContext) -> ActionPlan:
        return ActionPlan(
            action="app.focus",
            target=str(inp.pid),
            summary=f"Focus window of process {inp.pid}",
            payload={"pid": inp.pid},
        )

    async def _execute(
        self, inp: AppFocusInput, ctx: ToolContext, plan: ActionPlan
    ) -> AppFocusOutput:
        try:
            return AppFocusOutput(focused=self._apps.desktop.focus_window(inp.pid))
        except UnsupportedError as exc:
            raise ToolExecutionError(str(exc)) from exc


# ===================================================================== knowledge.search
class KnowledgeSearchInput(_Model):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=100)
    all_projects: bool = False
    collections: list[str] | None = Field(
        default=None, max_length=50, description="collection names/ids; omitted = all enabled"
    )


class KnowledgeHitOut(_Model):
    project_id: str
    uri: str
    start_line: int
    end_line: int
    snippet: str
    score: float
    privacy_mode: str
    tier: int = 1  # FR-020: 1 project, 2 company knowledge, 5 external sources
    source: str = "project"  # "project", a collection name, or "index" (all projects)


class KnowledgeSearchOutput(_Model):
    hits: list[KnowledgeHitOut]


class KnowledgeSearchTool(Tool[KnowledgeSearchInput, KnowledgeSearchOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="knowledge.search",
        version="1",
        description=(
            "Search indexed knowledge with provenance: the current project first, then "
            "company knowledge collections, then selected websites (FR-020)."
        ),
        input_model=KnowledgeSearchInput,
        output_model=KnowledgeSearchOutput,
        capabilities=frozenset({"knowledge.search"}),
        side_effect=SideEffect.NONE,
        timeout_s=30,
        cancellable=False,
        idempotent=True,
        privacy="Returns indexed content with its privacy label; content is untrusted.",
    )

    def __init__(
        self, knowledge: KnowledgeService, collections: CollectionService | None = None
    ) -> None:
        self._knowledge = knowledge
        self._collections = collections

    async def plan(self, inp: KnowledgeSearchInput, ctx: ToolContext) -> ActionPlan:
        scope = None if inp.all_projects or ctx.project is None else ctx.project.id
        return ActionPlan(
            action="knowledge.search",
            target=scope or "all",
            summary=f"Search knowledge for {inp.query!r}",
            payload={
                "query": inp.query,
                "project_id": scope,
                "all": inp.all_projects,
                "collections": inp.collections,
            },
        )

    async def _execute(
        self, inp: KnowledgeSearchInput, ctx: ToolContext, plan: ActionPlan
    ) -> KnowledgeSearchOutput:
        ranked: list[tuple[SearchHit, int, str]]
        if self._collections is not None and not plan.payload.get("all"):
            ranked = [
                (r.hit, r.tier, r.source)
                for r in await self._collections.search(
                    inp.query,
                    project_id=plan.payload["project_id"],
                    collections=plan.payload.get("collections"),
                    limit=inp.limit,
                )
            ]
        else:
            hits = await self._knowledge.search(
                inp.query, project_id=plan.payload["project_id"], limit=inp.limit
            )
            source = "project" if plan.payload["project_id"] else "index"
            ranked = [(h, 1, source) for h in hits]
        return KnowledgeSearchOutput(
            hits=[
                KnowledgeHitOut(
                    project_id=h.project_id,
                    uri=h.uri,
                    start_line=h.start_line,
                    end_line=h.end_line,
                    snippet=h.snippet,
                    score=h.score,
                    privacy_mode=h.privacy_mode,
                    tier=tier,
                    source=source,
                )
                for h, tier, source in ranked
            ]
        )
