"""Requirements domain (FR-008/009/010, BR-009/010, ADR-014).

* A requirement is a sequence of **immutable versions** identified by ``(key, version)``.
  Editing creates a new DRAFT version; approving it supersedes the previously approved one.
* Lifecycle: DRAFT → PENDING_APPROVAL → APPROVED | REJECTED, plus SUPERSEDED, and an
  orthogonal ``stale`` flag set when an approved upstream item changes.
* Hierarchy is by ``parent_key`` (e.g. BR → FR → EPIC → FEAT → US → AC); other relations
  are :class:`TraceLink` rows (FR → API, AC → TC, US → evidence...).
"""

import re
from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import Field, field_validator

from panos_core.domain.clock import utc_now
from panos_core.domain.ids import new_id
from panos_core.domain.models import AwareDatetime, Entity, NonEmptyStr, ProjectId, _id_of


class RequirementType(StrEnum):
    BR = "BR"  # business requirement
    FR = "FR"  # functional requirement
    NFR = "NFR"  # non-functional requirement
    RULE = "RULE"  # business rule / validation
    UC = "UC"  # use case
    EPIC = "EPIC"
    FEAT = "FEAT"
    US = "US"  # user story
    AC = "AC"  # acceptance criterion (key: AC-US001-01)
    TC = "TC"  # test case
    API = "API"  # API operation / contract element
    DATA = "DATA"  # data entity (ERD)
    ADR = "ADR"  # architecture decision (incl. the approved stack)
    RISK = "RISK"
    ASSUMPTION = "ASSUMPTION"
    DEPENDENCY = "DEPENDENCY"


class RequirementStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class TraceRelation(StrEnum):
    REFINES = "refines"  # FR refines BR, ...
    REALIZES = "realizes"  # API/DATA realizes FR
    VERIFIES = "verifies"  # TC verifies AC / US
    IMPLEMENTS = "implements"  # evidence (commit, file#symbol) implements US/FR
    DEPENDS_ON = "depends_on"
    RELATES = "relates"


class InterviewStatus(StrEnum):
    OPEN = "OPEN"  # questions waiting for answers
    READY = "READY"  # all questions answered (or skipped)
    COMPLETED = "COMPLETED"  # requirements generated


KEY_RE: Final = re.compile(r"^(?:[A-Z]{2,10}-\d{3}|AC-US\d{3}-\d{2}|NFR-[A-Z]{2,5}-\d{3})$")
EVIDENCE_RE: Final = re.compile(r"^(?:commit|file|job|test|workitem):[^\s]{1,500}$")

RequirementId = Annotated[str, _id_of("requirement")]
RequirementApprovalId = Annotated[str, _id_of("requirement_approval")]
TraceLinkId = Annotated[str, _id_of("trace_link")]
InterviewId = Annotated[str, _id_of("interview")]
SkillId = Annotated[str, _id_of("skill")]


def _key(value: str) -> str:
    if not KEY_RE.match(value):
        raise ValueError(f"invalid requirement key {value!r}")
    return value


class Requirement(Entity):
    """One immutable version of a requirement item."""

    id: RequirementId = Field(default_factory=lambda: new_id("requirement"))
    project_id: ProjectId
    key: str
    type: RequirementType
    version: int = Field(default=1, ge=1)
    parent_key: str | None = None
    title: NonEmptyStr
    body: str = ""
    status: RequirementStatus = RequirementStatus.DRAFT
    stale: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)  # priority, points, category...
    source: list[str] = Field(default_factory=list)  # provenance (brief, interview Q/A ids)
    created_by: NonEmptyStr = "user"
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @field_validator("key", "parent_key")
    @classmethod
    def _keys(cls, value: str | None) -> str | None:
        return None if value is None else _key(value)


class RequirementApproval(Entity):
    """A decision on one requirement version (ADR-014, AC-US031-03)."""

    id: RequirementApprovalId = Field(default_factory=lambda: new_id("requirement_approval"))
    project_id: ProjectId
    requirement_id: RequirementId
    key: str
    version: int = Field(ge=1)
    decision: RequirementStatus  # APPROVED or REJECTED
    decided_by: NonEmptyStr
    comments: str = ""
    cascaded_from_id: RequirementApprovalId | None = None
    excluded_keys: list[str] = Field(default_factory=list)
    decided_at: AwareDatetime = Field(default_factory=utc_now)


class TraceLink(Entity):
    """Version-independent link between items (by key) or to implementation evidence."""

    id: TraceLinkId = Field(default_factory=lambda: new_id("trace_link"))
    project_id: ProjectId
    source_key: str
    target: str  # a requirement key, or evidence like ``commit:<sha>`` / ``file:path#symbol``
    relation: TraceRelation
    created_by: NonEmptyStr = "user"
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @field_validator("source_key")
    @classmethod
    def _source(cls, value: str) -> str:
        return _key(value)

    @field_validator("target")
    @classmethod
    def _target(cls, value: str) -> str:
        if KEY_RE.match(value) or EVIDENCE_RE.match(value):
            return value
        raise ValueError(f"invalid trace target {value!r}")


class InterviewQuestion(Entity):
    id: str  # q1, q2, ...
    text: NonEmptyStr
    why: str = ""  # the ambiguity it resolves
    answer: str | None = None
    skipped: bool = False
    answered_by: str | None = None
    answered_at: AwareDatetime | None = None


class Interview(Entity):
    """Brief → ambiguity analysis → questions/answers; persisted and resumable (US-030)."""

    id: InterviewId = Field(default_factory=lambda: new_id("interview"))
    project_id: ProjectId
    brief: NonEmptyStr
    status: InterviewStatus = InterviewStatus.OPEN
    ambiguities: list[str] = Field(default_factory=list)
    questions: list[InterviewQuestion] = Field(default_factory=list)
    explicit_stack: str | None = None  # a stack pack the brief asked for (US-021)
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)


class GeneratedSkill(Entity):
    """A Claude skill generated for a project from approved facts (FR-030, US-033)."""

    id: SkillId = Field(default_factory=lambda: new_id("skill"))
    project_id: ProjectId
    name: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
    version: int = Field(default=1, ge=1)
    facts_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_keys: list[str] = Field(default_factory=list)  # approved items it was built from
    path: str
    generated_at: AwareDatetime = Field(default_factory=utc_now)


def key_number(key: str) -> int:
    """Trailing number of a key (FR-012 → 12, AC-US001-03 → 3)."""
    return int(key.rsplit("-", 1)[1])
