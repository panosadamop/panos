"""Estimated-versus-actual effort (BR-014/015, ADR-031, M10).

* :class:`EstimateBaseline`: what a project's stories were estimated at, recorded each time
  the project is estimated (versioned).
* :class:`EffortActual`: work done on a requirement. Human hours (``manual`` or
  ``tracker``) or machine metrics (``job``). Only *approved* human actuals feed calibration.
* :class:`Calibration`: a suggested or approved hours-per-point, overall or per technology.
  Estimation uses only approved calibrations.
"""

from datetime import date
from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, model_validator

from panos_core.domain.clock import utc_now
from panos_core.domain.ids import new_id
from panos_core.domain.models import AwareDatetime, Entity, NonEmptyStr, _id_of

BaselineId = Annotated[str, _id_of("baseline")]
ActualId = Annotated[str, _id_of("actual")]
CalibrationId = Annotated[str, _id_of("calibration")]


class EffortSource(StrEnum):
    MANUAL = "manual"  # logged by the owner
    TRACKER = "tracker"  # Jira worklog / Azure DevOps completed work
    JOB = "job"  # Panos job metrics (machine series)


class ActualStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class CalibrationStatus(StrEnum):
    SUGGESTED = "SUGGESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class EstimateBaseline(Entity):
    id: BaselineId = Field(default_factory=lambda: new_id("baseline"))
    project_id: str
    version: int = Field(ge=1)
    technology: str = "unknown"
    hours_per_point: float = Field(gt=0)
    calibration_id: str | None = None  # the calibration that produced it, if any
    stories: list[dict[str, Any]] = Field(default_factory=list)  # {key, points, hours}
    created_at: AwareDatetime = Field(default_factory=utc_now)


class EffortActual(Entity):
    id: ActualId = Field(default_factory=lambda: new_id("actual"))
    project_id: str
    requirement_key: NonEmptyStr
    source: EffortSource
    hours: float = Field(default=0.0, ge=0, le=10_000)  # human hours (manual/tracker)
    role: str = "developer"
    work_date: date
    external_ref: str | None = None  # tracker worklog id / job id (deduplication)
    machine: dict[str, float] = Field(default_factory=dict)  # job: runtime_s, tokens, cost_usd
    note: str = Field(default="", max_length=500)
    completes: bool = False  # the story is finished with this entry (calibration input)
    status: ActualStatus = ActualStatus.PENDING
    logged_by: str = "user"
    decided_by: str | None = None
    decided_at: AwareDatetime | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _shape(self) -> "EffortActual":
        if self.source == EffortSource.JOB and self.hours:
            raise ValueError("job actuals carry machine metrics, not human hours")
        if self.source != EffortSource.JOB and self.hours <= 0:
            raise ValueError("human actuals need hours > 0")
        return self


class Calibration(Entity):
    id: CalibrationId = Field(default_factory=lambda: new_id("calibration"))
    scope: NonEmptyStr  # "overall" | "technology:<name>"
    factor: float = Field(gt=0)  # actual ÷ estimated (median)
    hours_per_point: float = Field(gt=0)
    base_hours_per_point: float = Field(gt=0)
    stories: int = Field(ge=1)
    spread: float = Field(ge=0)  # interquartile range of the ratios
    bias: float  # mean (actual - estimated) / estimated
    mape: float = Field(ge=0)  # mean absolute percentage error of the original estimates
    basis: list[str] = Field(default_factory=list)  # project:story keys used
    status: CalibrationStatus = CalibrationStatus.SUGGESTED
    created_at: AwareDatetime = Field(default_factory=utc_now)
    decided_by: str | None = None
    decided_at: AwareDatetime | None = None
