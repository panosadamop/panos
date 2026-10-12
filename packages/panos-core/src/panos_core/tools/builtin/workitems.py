"""Jira / Azure DevOps tools (BR-016, US-042).

* ``workitem.search`` / ``workitem.get`` read freely (``pm.read``, P0, AC-US042-01).
* ``workitem.change`` prepares a change set into the exact HTTP requests and needs approval
  of that set (``pm.write``, P3, AC-US042-02). Created items can be linked to a requirement
  of the current project as ``workitem:`` evidence.
"""

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from panos_core.connectors import workitems as wi
from panos_core.connectors.http import ConnectorHttpError
from panos_core.connectors.service import ConnectorError, ConnectorService
from panos_core.domain.connectors import ConnectorAccount, ConnectorKind
from panos_core.domain.requirements import KEY_RE, TraceRelation
from panos_core.requirements.service import RequirementsError, RequirementsService
from panos_core.tools.contract import (
    ActionPlan,
    PlanRejectedError,
    SideEffect,
    Tool,
    ToolContext,
    ToolExecutionError,
    ToolSpec,
)

System = Literal["jira", "azure_devops"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _account(
    connectors: ConnectorService, account: str | None, system: System | None
) -> ConnectorAccount:
    try:
        if account:
            found = await connectors.get(account)
        elif system:
            found = await connectors.first(ConnectorKind(system))
        else:
            candidates = [
                a
                for a in await connectors.accounts()
                if a.kind in (ConnectorKind.JIRA, ConnectorKind.AZURE_DEVOPS)
            ]
            if len(candidates) != 1:
                raise PlanRejectedError(
                    "connect Jira or Azure DevOps first (panos connect jira|azure-devops)"
                    if not candidates
                    else "several trackers are connected; say which (system or account)"
                )
            found = candidates[0]
    except ConnectorError as exc:
        raise PlanRejectedError(str(exc)) from exc
    if found.kind not in (ConnectorKind.JIRA, ConnectorKind.AZURE_DEVOPS):
        raise PlanRejectedError(f"{found.label} is not a work-item tracker")
    return found


def _client(
    connectors: ConnectorService, account: ConnectorAccount, project: str | None
) -> tuple[Any, Any]:
    http = connectors.client(account)
    if account.kind == ConnectorKind.JIRA:
        return wi.JiraClient(http, account.base_url or ""), http
    return wi.AzureDevOpsClient(http, account.base_url or "", project), http


class WorkItemOut(_Model):
    key: str
    title: str
    status: str
    type: str
    assignee: str
    priority: str
    updated: str
    url: str
    description: str = ""


def _out(item: wi.WorkItem) -> WorkItemOut:
    return WorkItemOut(**item.__dict__)


# ===================================================================== workitem.search / get
class WorkItemSearchInput(_Model):
    query: str = Field(min_length=1, max_length=2000, description="text, JQL or WIQL")
    system: System | None = None
    account: str | None = None
    project: str | None = Field(default=None, max_length=128, description="ADO project")
    limit: int = Field(default=20, ge=1, le=100)


class WorkItemGetInput(_Model):
    key: str = Field(pattern=r"^(?:[A-Z][A-Z0-9_]+-[0-9]+|[0-9]+)$")
    system: System | None = None
    account: str | None = None
    project: str | None = Field(default=None, max_length=128)


class WorkItemListOutput(_Model):
    account: str
    system: str
    privacy_mode: str
    untrusted: bool = True
    items: list[WorkItemOut]


class WorkItemSearchTool(Tool[WorkItemSearchInput, WorkItemListOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="workitem.search",
        version="1",
        description="Search Jira issues (text or JQL) or Azure DevOps work items (text or WIQL).",
        input_model=WorkItemSearchInput,
        output_model=WorkItemListOutput,
        capabilities=frozenset({"pm.read"}),
        side_effect=SideEffect.NONE,
        timeout_s=60,
        cancellable=False,
        idempotent=True,
        privacy="Returns work items with the account's privacy label.",
    )

    def __init__(self, connectors: ConnectorService) -> None:
        self._connectors = connectors

    async def plan(self, inp: WorkItemSearchInput, ctx: ToolContext) -> ActionPlan:
        account = await _account(self._connectors, inp.account, inp.system)
        return ActionPlan(
            action="pm.read",
            target=account.id,
            summary=f"Search {account.label} for {inp.query[:80]!r}",
            payload={"query": inp.query, "project": inp.project},
        )

    async def _execute(
        self, inp: WorkItemSearchInput, ctx: ToolContext, plan: ActionPlan
    ) -> WorkItemListOutput:
        account = await self._connectors.get(plan.target)
        client, http = _client(self._connectors, account, inp.project)
        try:
            items = await client.search(inp.query, limit=inp.limit)
        except (ConnectorHttpError, ValueError) as exc:
            raise ToolExecutionError(str(exc)) from exc
        finally:
            await http.aclose()
        return WorkItemListOutput(
            account=account.label,
            system=account.kind.value,
            privacy_mode=account.privacy_mode.value,
            items=[_out(i) for i in items],
        )


class WorkItemGetTool(Tool[WorkItemGetInput, WorkItemListOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="workitem.get",
        version="1",
        description="Read one Jira issue or Azure DevOps work item.",
        input_model=WorkItemGetInput,
        output_model=WorkItemListOutput,
        capabilities=frozenset({"pm.read"}),
        side_effect=SideEffect.NONE,
        timeout_s=60,
        cancellable=False,
        idempotent=True,
        privacy="Returns the work item with the account's privacy label.",
    )

    def __init__(self, connectors: ConnectorService) -> None:
        self._connectors = connectors

    async def plan(self, inp: WorkItemGetInput, ctx: ToolContext) -> ActionPlan:
        account = await _account(self._connectors, inp.account, inp.system)
        return ActionPlan(
            action="pm.read",
            target=account.id,
            summary=f"Read {inp.key} in {account.label}",
            payload={"key": inp.key},
        )

    async def _execute(
        self, inp: WorkItemGetInput, ctx: ToolContext, plan: ActionPlan
    ) -> WorkItemListOutput:
        account = await self._connectors.get(plan.target)
        client, http = _client(self._connectors, account, inp.project)
        try:
            item = await client.get(inp.key)
        except (ConnectorHttpError, ValueError) as exc:
            raise ToolExecutionError(str(exc)) from exc
        finally:
            await http.aclose()
        return WorkItemListOutput(
            account=account.label,
            system=account.kind.value,
            privacy_mode=account.privacy_mode.value,
            items=[_out(item)],
        )


# ===================================================================== workitem.change
class ChangeIn(_Model):
    kind: Literal["create", "update", "transition", "comment"]
    key: str | None = Field(default=None, pattern=r"^(?:[A-Z][A-Z0-9_]+-[0-9]+|[0-9]+)$")
    project: str | None = Field(default=None, max_length=128)
    type: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=32_000)
    status: str | None = Field(default=None, max_length=64)
    comment: str | None = Field(default=None, max_length=32_000)
    requirement_key: str | None = Field(default=None, description="link a created item")

    @model_validator(mode="after")
    def _shape(self) -> "ChangeIn":
        if self.kind == "create" and not self.title:
            raise ValueError("create needs title")
        if self.kind != "create" and not self.key:
            raise ValueError(f"{self.kind} needs key")
        if self.kind == "transition" and not self.status:
            raise ValueError("transition needs status")
        if self.kind == "comment" and not self.comment:
            raise ValueError("comment needs comment")
        if self.kind == "update" and self.title is None and self.description is None:
            raise ValueError("update needs title or description")
        if self.requirement_key and not KEY_RE.match(self.requirement_key):
            raise ValueError("requirement_key must be a requirement key like US-003")
        return self


class WorkItemChangeInput(_Model):
    changes: list[ChangeIn] = Field(min_length=1, max_length=20)
    system: System | None = None
    account: str | None = None
    project: str | None = Field(default=None, max_length=128, description="ADO project")

    @model_validator(mode="after")
    def _projects(self) -> "WorkItemChangeInput":
        if any(c.kind == "create" and not (c.project or self.project) for c in self.changes):
            raise ValueError("create needs project (on the change or the change set)")
        return self


class AppliedChange(_Model):
    summary: str
    key: str | None
    url: str | None = None
    linked_requirement: str | None = None


class WorkItemChangeOutput(_Model):
    account: str
    applied: list[AppliedChange]


class WorkItemChangeTool(Tool[WorkItemChangeInput, WorkItemChangeOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="workitem.change",
        version="1",
        description=(
            "Create, update, transition or comment on work items. The whole prepared change "
            "set needs approval."
        ),
        input_model=WorkItemChangeInput,
        output_model=WorkItemChangeOutput,
        capabilities=frozenset({"pm.write"}),
        side_effect=SideEffect.EXTERNAL,
        timeout_s=120,
        cancellable=False,
        idempotent=False,
        privacy="Writes to the connected tracker after approval of the exact requests.",
    )

    def __init__(
        self, connectors: ConnectorService, requirements: RequirementsService | None = None
    ) -> None:
        self._connectors = connectors
        self._requirements = requirements

    async def plan(self, inp: WorkItemChangeInput, ctx: ToolContext) -> ActionPlan:
        account = await _account(self._connectors, inp.account, inp.system)
        client, http = _client(self._connectors, account, inp.project)
        prepared = []
        try:
            for change in inp.changes:
                req = await client.prepare(
                    wi.Change(
                        kind=change.kind,
                        key=change.key,
                        project=change.project or inp.project,
                        type=change.type,
                        title=change.title,
                        description=change.description,
                        status=change.status,
                        comment=change.comment,
                    )
                )
                prepared.append(
                    {
                        **req.as_dict(),
                        "item_key": change.key,
                        "requirement_key": change.requirement_key,
                    }
                )
        except (ConnectorHttpError, ValueError) as exc:
            raise PlanRejectedError(str(exc)) from exc
        finally:
            await http.aclose()
        if any(p["requirement_key"] for p in prepared) and ctx.project is None:
            raise PlanRejectedError("linking to a requirement needs a project (-p)")
        return ActionPlan(
            action="pm.write",
            target=account.id,
            summary="; ".join(p["summary"] for p in prepared)[:1000],
            payload={
                "account": account.id,
                "system": account.kind.value,
                "requests": prepared,
                "project_id": ctx.project.id if ctx.project else None,
            },
        )

    async def _execute(
        self, inp: WorkItemChangeInput, ctx: ToolContext, plan: ActionPlan
    ) -> WorkItemChangeOutput:
        account = await self._connectors.get(plan.payload["account"])
        http = self._connectors.client(account)
        applied: list[AppliedChange] = []
        try:
            for req in plan.payload["requests"]:
                [result] = await wi.apply(http, [req])
                # Existing items keep their key (a comment's id is not a work-item key).
                key = req.get("item_key") or result.get("key")
                if key is None and "id" in result:
                    key = str(result["id"])
                url = None
                if key and account.kind == ConnectorKind.JIRA:
                    url = f"{account.base_url}/browse/{key}"
                elif key:
                    url = (result.get("_links") or {}).get("html", {}).get("href")
                linked = await self._link(plan, req, key, ctx)
                applied.append(
                    AppliedChange(
                        summary=req["summary"], key=key, url=url, linked_requirement=linked
                    )
                )
        except ConnectorHttpError as exc:
            done = "; ".join(a.summary for a in applied) or "nothing"
            raise ToolExecutionError(
                f"{exc} (already applied: {done})", {"applied": [a.model_dump() for a in applied]}
            ) from exc
        finally:
            await http.aclose()
        await self._connectors.touch(account)
        return WorkItemChangeOutput(account=account.label, applied=applied)

    async def _link(
        self, plan: ActionPlan, req: dict[str, Any], key: str | None, ctx: ToolContext
    ) -> str | None:
        requirement: str | None = req.get("requirement_key")
        project_id = plan.payload.get("project_id")
        if not (requirement and key and project_id and self._requirements):
            return None
        try:
            await self._requirements.link(
                project_id,
                requirement,
                f"workitem:{plan.payload['system']}:{key}",
                TraceRelation.IMPLEMENTS,
                actor=ctx.actor,
            )
        except RequirementsError:
            return None  # the external write happened; a missing requirement is not fatal
        return requirement


WORKITEM_TOOLS = (WorkItemSearchTool, WorkItemGetTool)
