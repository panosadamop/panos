"""Jobs: start, inspect, pause/resume/cancel, roll back (API.md /jobs, FR-022/024, US-050/052)."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from panos_core.checkpoints import CheckpointError
from panos_core.domain.enums import JobStatus
from panos_core.domain.errors import EntityNotFoundError
from panos_core.domain.models import Job, JobBudget
from panos_core.jobs.runner import JobError
from panos_core.projects.service import ProjectError
from panos_node_api.routers.audit import _audit_view
from panos_node_api.routers.deps import LOCAL_USER, Container, raise_for_reason
from panos_node_api.schemas import AuditEventView

router = APIRouter(tags=["jobs"])


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=3, max_length=10_000)
    project: str | None = Field(default=None, description="project id or name")
    kind: str = "coding"
    commit: bool = True  # coding: request commit approval at the end (never auto-commits)
    argv: list[str] | None = Field(default=None, max_length=256)  # command jobs
    # research jobs: read these pages instead of searching; how many sources to use
    urls: list[str] | None = Field(default=None, max_length=10)
    max_sources: int = Field(default=5, ge=1, le=10)
    budget: JobBudget = Field(default_factory=JobBudget)


class StepView(BaseModel):
    sequence: int
    agent: str | None
    tool: str | None
    status: str
    input_summary: str | None
    output_summary: str | None
    started_at: datetime | None
    completed_at: datetime | None


class CheckpointView(BaseModel):
    id: str
    reference: str
    created_at: datetime


class JobView(BaseModel):
    id: str
    kind: str
    objective: str
    status: str
    status_reason: str
    project_id: str | None
    phase: str | None
    pending_approval: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    running: bool
    budget: dict[str, Any]


class JobDetail(JobView):
    result: dict[str, Any] | None
    steps: list[StepView]
    checkpoints: list[CheckpointView]


class JobList(BaseModel):
    jobs: list[JobView]


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str | None = None  # default: the job's first checkpoint


class RollbackView(BaseModel):
    checkpoint_id: str
    restored: list[str]
    deleted: list[str]
    safety_reference: str
    verified: bool


def _view(container: Any, job: Job) -> JobView:
    pending = job.state.get("pending_approval") or {}
    return JobView(
        id=job.id,
        kind=job.kind,
        objective=job.objective,
        status=job.status.value,
        status_reason=job.status_reason,
        project_id=job.project_id,
        phase=job.state.get("phase"),
        pending_approval=pending.get("approval_id")
        if job.status == JobStatus.WAITING_FOR_APPROVAL
        else None,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        running=container.runner.is_running(job.id),
        budget=job.budget.model_dump(),
    )


async def _job(container: Any, job_id: str) -> Job:
    try:
        job: Job = await container.jobs.get(job_id)
    except EntityNotFoundError as exc:
        raise_for_reason("not_found", str(exc))
    return job


@router.post("/jobs", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
async def create_job(body: JobCreate, container: Container) -> JobView:
    project = None
    if body.project:
        try:
            project = await container.projects.get(body.project)
        except ProjectError as exc:
            raise_for_reason(exc.reason, str(exc))
    if body.kind == "coding" and project is None:
        raise_for_reason("unsupported", "a coding job needs a project")
    if body.kind == "command" and not body.argv:
        raise_for_reason("unsupported", "a command job needs argv")
    job_input: dict[str, Any] = {"commit": body.commit} if body.kind == "coding" else {}
    if body.argv:
        job_input["argv"] = body.argv
    if body.kind == "research":
        job_input = {
            "question": body.objective,
            "urls": body.urls or [],
            "max_sources": body.max_sources,
        }
    try:
        job = await container.runner.submit(
            body.objective,
            kind=body.kind,
            project=project,
            input=job_input,
            budget=body.budget,
            actor=LOCAL_USER,
        )
    except JobError as exc:
        raise_for_reason(exc.reason, str(exc))
    return _view(container, job)


@router.get("/jobs", response_model=JobList)
async def list_jobs(
    container: Container,
    status_filter: Annotated[list[JobStatus] | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> JobList:
    jobs = await container.jobs.list(statuses=status_filter, limit=limit)
    return JobList(jobs=[_view(container, j) for j in jobs])


@router.get("/jobs/{job_id}", response_model=JobDetail)
async def get_job(job_id: str, container: Container) -> JobDetail:
    job = await _job(container, job_id)
    steps = await container.jobs.steps(job_id)
    checkpoints = await container.checkpoints.for_job(job_id)
    return JobDetail(
        **_view(container, job).model_dump(),
        result=job.result or job.state.get("report"),  # report so far while waiting
        steps=[
            StepView(
                sequence=s.sequence,
                agent=s.agent_type,
                tool=s.tool_name,
                status=s.status.value,
                input_summary=s.input_summary,
                output_summary=s.output_summary,
                started_at=s.started_at,
                completed_at=s.completed_at,
            )
            for s in steps
        ],
        checkpoints=[
            CheckpointView(id=c.id, reference=c.reference, created_at=c.created_at)
            for c in checkpoints
        ],
    )


async def _control(container: Any, job_id: str, action: str) -> JobView:
    await _job(container, job_id)
    try:
        job = await getattr(container.runner, action)(job_id, actor=LOCAL_USER)
    except JobError as exc:
        raise_for_reason(exc.reason, str(exc))
    return _view(container, job)


@router.post("/jobs/{job_id}/pause", response_model=JobView)
async def pause_job(job_id: str, container: Container) -> JobView:
    return await _control(container, job_id, "pause")


@router.post("/jobs/{job_id}/resume", response_model=JobView)
async def resume_job(job_id: str, container: Container) -> JobView:
    return await _control(container, job_id, "resume")


@router.post("/jobs/{job_id}/cancel", response_model=JobView)
async def cancel_job(job_id: str, container: Container) -> JobView:
    return await _control(container, job_id, "cancel")


@router.post("/jobs/{job_id}/rollback", response_model=RollbackView)
async def rollback_job(job_id: str, body: RollbackRequest, container: Container) -> RollbackView:
    job = await _job(container, job_id)
    if container.runner.is_running(job_id):
        raise_for_reason("invalid_state", "pause or cancel the job before rolling back")
    checkpoints = await container.checkpoints.for_job(job_id)
    try:
        if body.checkpoint_id:
            checkpoint = await container.checkpoints.get(body.checkpoint_id)
            if checkpoint.job_id != job_id:
                raise CheckpointError("mismatch", "checkpoint belongs to another job")
        elif checkpoints:
            checkpoint = checkpoints[0]
        else:
            raise CheckpointError("not_found", "this job has no checkpoint")
        if job.project_id is None:
            raise CheckpointError("not_found", "job has no project")
        project = await container.projects.get(job.project_id)
        report = await container.checkpoints.rollback(project, checkpoint, actor=LOCAL_USER)
    except CheckpointError as exc:
        raise_for_reason(exc.reason, str(exc))
    return RollbackView(**report.__dict__)


@router.get("/jobs/{job_id}/events", response_model=list[AuditEventView])
async def job_events(
    job_id: str, container: Container, limit: Annotated[int, Query(ge=1, le=1000)] = 200
) -> list[AuditEventView]:
    await _job(container, job_id)
    events = await container.audit.list(job_id=job_id, limit=limit)
    return [_audit_view(e) for e in events]
