"""Requirements pipeline API (API.md "Requirements", FR-007..010, US-030..033)."""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from panos_core.agents import AgentError
from panos_core.ai.ports import ProviderError
from panos_core.ai.privacy import PrivacyViolationError
from panos_core.ai.router import NoRouteError
from panos_core.domain import Project
from panos_core.domain.requirements import (
    Interview,
    Requirement,
    RequirementApproval,
    RequirementStatus,
    RequirementType,
    TraceRelation,
)
from panos_core.projects.service import ProjectError
from panos_core.requirements import RequirementsError
from panos_node_api.problems import problem_response
from panos_node_api.routers.conversation import ai_error_response
from panos_node_api.routers.deps import LOCAL_USER, Container, raise_for_reason

router = APIRouter(tags=["requirements"])
AI_ERRORS = (NoRouteError, PrivacyViolationError, ProviderError, AgentError)


# ---------------------------------------------------------------------- schemas
class InterviewStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brief: str = Field(min_length=10, max_length=50_000)
    name: str | None = Field(default=None, max_length=100, description="new project name")
    project: str | None = Field(default=None, description="existing project id or name")


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    answer: str | None = Field(default=None, max_length=10_000)  # null = skip


class RequirementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: RequirementType
    title: str = Field(min_length=1, max_length=2000)
    body: str = Field(default="", max_length=50_000)
    parent_key: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class RequirementEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=2000)
    body: str | None = Field(default=None, max_length=50_000)
    attributes: dict[str, Any] | None = None


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    comments: str = Field(default="", max_length=5000)
    cascade: bool = True
    exclude: list[str] = Field(default_factory=list)


class ClearStaleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: list[str] = Field(min_length=1)
    reason: str = Field(min_length=3, max_length=2000)


class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str
    target: str
    relation: TraceRelation


class ScaffoldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_ide: bool = False
    git_init: bool = True


class RequirementView(BaseModel):
    id: str
    key: str
    type: str
    version: int
    parent_key: str | None
    title: str
    body: str
    status: str
    stale: bool
    attributes: dict[str, Any]
    source: list[str]
    created_by: str
    created_at: datetime


class ApprovalView(BaseModel):
    id: str
    key: str
    version: int
    decision: str
    decided_by: str
    comments: str
    cascaded_from_id: str | None
    excluded_keys: list[str]
    decided_at: datetime


class RequirementDetail(RequirementView):
    history: list[RequirementView]
    approvals: list[ApprovalView]


class InterviewView(BaseModel):
    id: str
    project_id: str
    brief: str
    status: str
    explicit_stack: str | None
    ambiguities: list[str]
    questions: list[dict[str, Any]]


def _req(r: Requirement) -> RequirementView:
    return RequirementView(**r.model_dump(mode="json") | {"created_at": r.created_at})


def _appr(a: RequirementApproval) -> ApprovalView:
    return ApprovalView(**a.model_dump(mode="json") | {"decided_at": a.decided_at})


def _itv(i: Interview) -> InterviewView:
    data = i.model_dump(mode="json")
    return InterviewView(**{k: data[k] for k in InterviewView.model_fields})


async def _project(container: Any, ref: str) -> Project:
    try:
        project: Project = await container.projects.get(ref)
    except ProjectError as exc:
        raise_for_reason(exc.reason, str(exc))
    return project


def _ai(exc: Exception) -> JSONResponse:
    if isinstance(exc, AgentError):
        return problem_response(
            502, f"the model returned unusable output: {exc}", reason="agent_output"
        )
    return ai_error_response(exc)


# ---------------------------------------------------------------------- interviews
@router.post("/interviews", response_model=InterviewView, status_code=status.HTTP_201_CREATED)
async def start_interview(body: InterviewStart, container: Container) -> Any:
    project = await _project(container, body.project) if body.project else None
    try:
        interview = await container.pipeline.start(
            body.brief, project=project, name=body.name, actor=LOCAL_USER
        )
    except (RequirementsError, ProjectError) as exc:
        raise_for_reason(exc.reason, str(exc))
    except AI_ERRORS as exc:
        return _ai(exc)
    return _itv(interview)


@router.get("/interviews/{interview_id}", response_model=InterviewView)
async def get_interview(interview_id: str, container: Container) -> InterviewView:
    try:
        return _itv(await container.pipeline.get_interview(interview_id))
    except RequirementsError as exc:
        raise_for_reason(exc.reason, str(exc))


@router.post("/interviews/{interview_id}/answers", response_model=InterviewView)
async def answer(interview_id: str, body: AnswerRequest, container: Container) -> InterviewView:
    try:
        return _itv(
            await container.pipeline.answer(
                interview_id, body.question_id, body.answer, actor=LOCAL_USER
            )
        )
    except RequirementsError as exc:
        raise_for_reason(exc.reason, str(exc))


@router.post("/interviews/{interview_id}/complete", response_model=list[RequirementView])
async def complete_interview(interview_id: str, container: Container) -> Any:
    try:
        items = await container.pipeline.complete(interview_id, actor=LOCAL_USER)
    except RequirementsError as exc:
        raise_for_reason(exc.reason, str(exc))
    except AI_ERRORS as exc:
        return _ai(exc)
    return [_req(i) for i in items]


# ---------------------------------------------------------------------- requirements
@router.get("/projects/{ref}/requirements", response_model=list[RequirementView])
async def list_requirements(
    ref: str,
    container: Container,
    type: RequirementType | None = None,
    status_filter: Annotated[RequirementStatus | None, Query(alias="status")] = None,
) -> list[RequirementView]:
    project = await _project(container, ref)
    items = await container.requirements.list_latest(project.id, type=type, status=status_filter)
    return [_req(i) for i in items]


@router.post(
    "/projects/{ref}/requirements",
    response_model=RequirementView,
    status_code=status.HTTP_201_CREATED,
)
async def create_requirement(
    ref: str, body: RequirementCreate, container: Container
) -> RequirementView:
    project = await _project(container, ref)
    try:
        item = await container.requirements.create(
            project.id,
            body.type,
            body.title,
            body=body.body,
            parent_key=body.parent_key,
            attributes=body.attributes,
            actor=LOCAL_USER,
        )
    except RequirementsError as exc:
        raise_for_reason(exc.reason, str(exc))
    return _req(item)


@router.get("/projects/{ref}/requirements/{key}", response_model=RequirementDetail)
async def get_requirement(ref: str, key: str, container: Container) -> RequirementDetail:
    project = await _project(container, ref)
    try:
        item = await container.requirements.get(project.id, key)
    except RequirementsError as exc:
        raise_for_reason(exc.reason, str(exc))
    history = await container.requirements.history(project.id, key)
    approvals = await container.requirements.approvals(project.id, key)
    return RequirementDetail(
        **_req(item).model_dump(),
        history=[_req(h) for h in history],
        approvals=[_appr(a) for a in approvals],
    )


@router.patch("/projects/{ref}/requirements/{key}")
async def edit_requirement(
    ref: str, key: str, body: RequirementEdit, container: Container
) -> dict[str, Any]:
    project = await _project(container, ref)
    try:
        item, stale = await container.requirements.edit(
            project.id,
            key,
            actor=LOCAL_USER,
            title=body.title,
            body=body.body,
            attributes=body.attributes,
        )
    except RequirementsError as exc:
        raise_for_reason(exc.reason, str(exc))
    return {"requirement": _req(item).model_dump(mode="json"), "stale": stale}


@router.post("/projects/{ref}/requirements/{key}/submit", response_model=RequirementView)
async def submit_requirement(ref: str, key: str, container: Container) -> RequirementView:
    project = await _project(container, ref)
    try:
        return _req(await container.requirements.submit(project.id, key, actor=LOCAL_USER))
    except RequirementsError as exc:
        raise_for_reason(exc.reason, str(exc))


@router.post("/projects/{ref}/requirements/{key}/approval", response_model=list[ApprovalView])
async def decide_requirement(
    ref: str, key: str, body: ApprovalRequest, container: Container
) -> list[ApprovalView]:
    project = await _project(container, ref)
    try:
        if body.decision == "approve":
            records = await container.requirements.approve(
                project.id,
                key,
                actor=LOCAL_USER,
                comments=body.comments,
                cascade=body.cascade,
                exclude=body.exclude,
            )
        else:
            records = [
                await container.requirements.reject(
                    project.id, key, actor=LOCAL_USER, comments=body.comments
                )
            ]
    except RequirementsError as exc:
        raise_for_reason(exc.reason, str(exc))
    return [_appr(r) for r in records]


@router.post("/projects/{ref}/requirements/clear-stale")
async def clear_stale(ref: str, body: ClearStaleRequest, container: Container) -> dict[str, Any]:
    project = await _project(container, ref)
    cleared = await container.requirements.clear_stale(
        project.id, body.keys, actor=LOCAL_USER, reason=body.reason
    )
    return {"cleared": cleared}


@router.post("/projects/{ref}/links", status_code=status.HTTP_201_CREATED)
async def add_link(ref: str, body: LinkRequest, container: Container) -> dict[str, Any]:
    project = await _project(container, ref)
    try:
        link = await container.requirements.link(
            project.id, body.source_key, body.target, body.relation, actor=LOCAL_USER
        )
    except RequirementsError as exc:
        raise_for_reason(exc.reason, str(exc))
    return link.model_dump(mode="json")


@router.delete("/projects/{ref}/links", status_code=status.HTTP_204_NO_CONTENT)
async def remove_link(ref: str, body: LinkRequest, container: Container) -> None:
    project = await _project(container, ref)
    if not await container.requirements.unlink(
        project.id, body.source_key, body.target, body.relation
    ):
        raise_for_reason("not_found", "no such link")


@router.get("/projects/{ref}/traceability")
async def traceability(ref: str, container: Container, root: str | None = None) -> dict[str, Any]:
    project = await _project(container, ref)
    try:
        result: dict[str, Any] = await container.requirements.traceability(project.id, root)
    except RequirementsError as exc:
        raise_for_reason(exc.reason, str(exc))
    return result


# ---------------------------------------------------------------------- outputs
@router.get("/projects/{ref}/estimate")
async def project_estimate(ref: str, container: Container) -> dict[str, Any]:
    project = await _project(container, ref)
    result: dict[str, Any] = (await container.pipeline.estimate(project)).as_dict()
    return result


@router.post("/projects/{ref}/documents")
async def generate_documents(ref: str, container: Container) -> dict[str, Any]:
    project = await _project(container, ref)
    return {"files": await container.pipeline.documents(project, actor=LOCAL_USER)}


@router.post("/projects/{ref}/skills")
async def generate_skills(ref: str, container: Container) -> dict[str, Any]:
    project = await _project(container, ref)
    return {"skills": await container.pipeline.skills(project, actor=LOCAL_USER)}


@router.post("/projects/{ref}/scaffold")
async def scaffold(ref: str, body: ScaffoldRequest, container: Container) -> dict[str, Any]:
    project = await _project(container, ref)
    try:
        report = await container.pipeline.scaffold(
            project, actor=LOCAL_USER, git_init=body.git_init
        )
    except RequirementsError as exc:
        raise_for_reason(exc.reason, str(exc))
    result: dict[str, Any] = dict(report.__dict__)
    if body.open_ide:
        await container.analysis.analyze(project, actor=LOCAL_USER)  # detect the new stack
        ctx = await container.context_for(project, actor=LOCAL_USER)
        opened = await container.tools.invoke("project.open", {}, ctx)
        result["opened"] = {
            "status": opened.status.value,
            "error": opened.error,
            "output": opened.output,
        }
    return result
