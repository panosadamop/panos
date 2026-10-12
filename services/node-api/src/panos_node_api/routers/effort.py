"""Estimated-versus-actual effort and calibration (BR-014/015, ADR-031, M10-01..05)."""

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from panos_core.domain.effort import Calibration, EffortActual
from panos_core.effort.service import EffortError
from panos_core.effort.trackers import import_worklogs
from panos_core.projects.service import ProjectError
from panos_node_api.routers.deps import LOCAL_USER, Container, raise_for_reason

router = APIRouter(tags=["effort"])


async def _project(container: Any, ref: str) -> Any:
    try:
        return await container.projects.get(ref)
    except ProjectError as exc:
        raise_for_reason(exc.reason, str(exc))


class ActualView(BaseModel):
    id: str
    requirement_key: str
    source: str
    hours: float
    role: str
    work_date: str
    external_ref: str | None
    machine: dict[str, float]
    note: str
    completes: bool
    status: str
    logged_by: str
    decided_by: str | None


def actual_view(a: EffortActual) -> ActualView:
    return ActualView(
        id=a.id,
        requirement_key=a.requirement_key,
        source=a.source.value,
        hours=a.hours,
        role=a.role,
        work_date=a.work_date.isoformat(),
        external_ref=a.external_ref,
        machine=a.machine,
        note=a.note,
        completes=a.completes,
        status=a.status.value,
        logged_by=a.logged_by,
        decided_by=a.decided_by,
    )


class CalibrationView(BaseModel):
    id: str
    scope: str
    factor: float
    hours_per_point: float
    base_hours_per_point: float
    stories: int
    spread: float
    bias: float
    mape: float
    status: str
    created_at: str
    decided_by: str | None


def calibration_view(c: Calibration) -> CalibrationView:
    return CalibrationView(
        id=c.id,
        scope=c.scope,
        factor=c.factor,
        hours_per_point=c.hours_per_point,
        base_hours_per_point=c.base_hours_per_point,
        stories=c.stories,
        spread=c.spread,
        bias=c.bias,
        mape=c.mape,
        status=c.status.value,
        created_at=c.created_at.isoformat(),
        decided_by=c.decided_by,
    )


class LogIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_key: str = Field(min_length=3, max_length=32)
    hours: float = Field(gt=0, le=200)
    role: str = Field(default="developer", max_length=32)
    work_date: date | None = None
    note: str = Field(default="", max_length=500)
    completes: bool = False


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]


@router.post(
    "/projects/{ref}/effort", response_model=ActualView, status_code=status.HTTP_201_CREATED
)
async def log_effort(ref: str, body: LogIn, container: Container) -> Any:
    project = await _project(container, ref)
    try:
        actual = await container.effort.log(
            project,
            body.requirement_key,
            body.hours,
            role=body.role,
            work_date=body.work_date,
            note=body.note,
            completes=body.completes,
            actor=LOCAL_USER,
        )
    except EffortError as exc:
        raise_for_reason(exc.reason, str(exc))
    return actual_view(actual)


@router.get("/projects/{ref}/effort", response_model=list[ActualView])
async def list_effort(
    ref: str,
    container: Container,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    source: str | None = None,
) -> Any:
    project = await _project(container, ref)
    return [
        actual_view(a)
        for a in await container.effort.actuals(project, status=status_filter, source=source)
    ]


@router.get("/projects/{ref}/effort/report")
async def effort_report(ref: str, container: Container) -> dict[str, Any]:
    project = await _project(container, ref)
    report: dict[str, Any] = await container.effort.report(project)
    return report


@router.post("/projects/{ref}/effort/import")
async def import_tracker(ref: str, container: Container) -> dict[str, Any]:
    """Tracker worklogs of linked work items, as pending actuals (M10-03)."""
    project = await _project(container, ref)
    result = await import_worklogs(
        project,
        connectors=container.connectors,
        requirements=container.requirements,
        effort=container.effort,
        actor=LOCAL_USER,
    )
    return result.__dict__


@router.post("/projects/{ref}/estimate/baseline")
async def record_baseline(ref: str, container: Container) -> dict[str, Any]:
    """Estimate now and keep it as the baseline for estimate-vs-actual (ADR-031 §1)."""
    project = await _project(container, ref)
    result: dict[str, Any] = (
        await container.pipeline.estimate(project, record=True, actor=LOCAL_USER)
    ).as_dict()
    return result


@router.post("/effort/{actual_id}/decision", response_model=ActualView)
async def decide_actual(actual_id: str, body: DecisionIn, container: Container) -> Any:
    try:
        actual = await container.effort.decide(
            actual_id, approve=body.decision == "approve", actor=LOCAL_USER
        )
    except EffortError as exc:
        raise_for_reason(exc.reason, str(exc))
    return actual_view(actual)


@router.get("/effort/calibrations", response_model=list[CalibrationView])
async def list_calibrations(
    container: Container, status_filter: Annotated[str | None, Query(alias="status")] = None
) -> Any:
    return [calibration_view(c) for c in await container.effort.calibrations(status=status_filter)]


@router.post("/effort/calibrations/suggest", response_model=list[CalibrationView])
async def suggest(container: Container) -> Any:
    return [calibration_view(c) for c in await container.effort.suggest(actor=LOCAL_USER)]


@router.post("/effort/calibrations/{calibration_id}/decision", response_model=CalibrationView)
async def decide_calibration(calibration_id: str, body: DecisionIn, container: Container) -> Any:
    try:
        done = await container.effort.decide_calibration(
            calibration_id, approve=body.decision == "approve", actor=LOCAL_USER
        )
    except EffortError as exc:
        raise_for_reason(exc.reason, str(exc))
    return calibration_view(done)
