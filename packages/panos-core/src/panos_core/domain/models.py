"""Core domain entities (DATA_MODEL.md, as amended by ADR-006).

Entities validate their own invariants. State-transition rules (job state machine,
approval lifecycle) are added by the services that own them in later milestones.
"""

from datetime import datetime
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from panos_core.domain.clock import utc_now
from panos_core.domain.enums import (
    ApprovalStatus,
    CheckpointType,
    JobStatus,
    NodeStatus,
    PermissionLevel,
    PolicyDecision,
    PrivacyMode,
    ProjectStatus,
    StepStatus,
)
from panos_core.domain.ids import is_valid_id, new_id


def _id_of(kind: str) -> AfterValidator:
    def check(value: str) -> str:
        if not is_valid_id(value, kind):
            raise ValueError(f"not a valid {kind} id: {value!r}")
        return value

    return AfterValidator(check)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


ProjectId = Annotated[str, _id_of("project")]
JobId = Annotated[str, _id_of("job")]
StepId = Annotated[str, _id_of("step")]
ApprovalId = Annotated[str, _id_of("approval")]
AuditId = Annotated[str, _id_of("audit")]
NodeId = Annotated[str, _id_of("node")]
CheckpointId = Annotated[str, _id_of("checkpoint")]
SnapshotId = Annotated[str, _id_of("snapshot")]
ApplicationId = Annotated[str, _id_of("application")]
PreferenceId = Annotated[str, _id_of("preference")]
ConversationId = Annotated[str, _id_of("conversation")]
MessageId = Annotated[str, _id_of("message")]
UsageId = Annotated[str, _id_of("usage")]
AwareDatetime = Annotated[datetime, AfterValidator(_aware)]
NonEmptyStr = Annotated[str, Field(min_length=1, max_length=10_000)]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, frozen=False)


class Node(Entity):
    """A Panos installation. In the MVP there is exactly one local node."""

    id: NodeId = Field(default_factory=lambda: new_id("node"))
    name: NonEmptyStr
    platform: NonEmptyStr
    capabilities: list[str] = Field(default_factory=list)
    public_identity: str | None = None
    status: NodeStatus = NodeStatus.ONLINE
    last_seen_at: AwareDatetime = Field(default_factory=utc_now)


class Project(Entity):
    """A registered project (FR-004). New projects default to LOCAL_ONLY (ADR-024)."""

    id: ProjectId = Field(default_factory=lambda: new_id("project"))
    name: NonEmptyStr
    path: NonEmptyStr
    node_id: NodeId
    privacy_mode: PrivacyMode = PrivacyMode.LOCAL_ONLY
    preferred_ide: str | None = None  # application key, e.g. "jetbrains.pycharm"
    status: ProjectStatus = ProjectStatus.ACTIVE
    created_at: AwareDatetime = Field(default_factory=utc_now)
    # Per-project permission overrides (ADR-005): may tighten; loosening limited by policy.
    policy_overrides: dict[str, PermissionLevel] = Field(default_factory=dict)
    # Free-form AI policy settings (budgets, model preferences); interpreted in M4.
    ai_policy: dict[str, Any] = Field(default_factory=dict)
    knowledge_enabled: bool = True

    @model_validator(mode="after")
    def _path_is_absolute(self) -> Self:
        # Accept either flavour: a Windows drive/UNC path or a POSIX root path.
        candidates: tuple[PurePath, ...] = (PureWindowsPath(self.path), PurePosixPath(self.path))
        if not any(p.is_absolute() for p in candidates):
            raise ValueError(f"project path must be absolute: {self.path!r}")
        return self


class JobBudget(BaseModel):
    """Job limits (FR-023). ``None`` means "use the configured default"."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_runtime_s: int | None = Field(default=None, gt=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, gt=0)
    max_repair_attempts: int | None = Field(default=None, ge=0)


class Job(Entity):
    id: JobId = Field(default_factory=lambda: new_id("job"))
    project_id: ProjectId | None = None
    node_id: NodeId
    objective: NonEmptyStr
    status: JobStatus = JobStatus.QUEUED
    budget: JobBudget = Field(default_factory=JobBudget)
    # Workflow that runs the job, its input, resumable progress and final report (ADR-011).
    kind: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")] = "coding"
    input: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    status_reason: str = ""
    created_at: AwareDatetime = Field(default_factory=utc_now)
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _timestamps_ordered(self) -> Self:
        if self.started_at and self.completed_at and self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        return self


class JobStep(Entity):
    id: StepId = Field(default_factory=lambda: new_id("step"))
    job_id: JobId
    sequence: int = Field(ge=1)
    agent_type: str | None = None
    tool_name: str | None = None
    status: StepStatus = StepStatus.PENDING
    input_summary: str | None = None
    output_summary: str | None = None
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None


class ActionApproval(Entity):
    """Approval of one exact proposed tool action (PERMISSIONS.md, ADR-006)."""

    id: ApprovalId = Field(default_factory=lambda: new_id("approval"))
    project_id: ProjectId | None = None
    job_id: JobId | None = None
    action_type: NonEmptyStr
    target: NonEmptyStr
    payload_hash: Sha256Hex
    summary: NonEmptyStr
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: str | None = None
    requested_at: AwareDatetime = Field(default_factory=utc_now)
    decided_at: AwareDatetime | None = None
    expires_at: AwareDatetime
    consumed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _expiry_after_request(self) -> Self:
        if self.expires_at <= self.requested_at:
            raise ValueError("expires_at must be after requested_at")
        return self


class AuditEvent(Entity):
    """A security-relevant or consequential event (FR-028)."""

    id: AuditId = Field(default_factory=lambda: new_id("audit"))
    node_id: NodeId
    project_id: ProjectId | None = None
    job_id: JobId | None = None
    actor: NonEmptyStr
    event_type: NonEmptyStr
    action: NonEmptyStr
    policy_decision: PolicyDecision | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: AwareDatetime = Field(default_factory=utc_now)
    # Tamper-evidence chain (ADR-020); set by the audit service when the event is recorded.
    prev_hash: str = ""
    hash: str = ""


class Checkpoint(Entity):
    id: CheckpointId = Field(default_factory=lambda: new_id("checkpoint"))
    project_id: ProjectId
    job_id: JobId | None = None
    type: CheckpointType = CheckpointType.SHADOW_GIT
    reference: NonEmptyStr
    created_at: AwareDatetime = Field(default_factory=utc_now)


class RepositorySnapshot(Entity):
    """Result of repository onboarding (FR-005, AC-US012-01..03).

    ``stack`` holds languages/frameworks/package managers; ``commands`` the discovered
    build/test/lint commands; ``structure`` the file/module map, conventions, docs and
    Git state; ``clarifications`` the questions to resolve before material changes.
    """

    id: SnapshotId = Field(default_factory=lambda: new_id("snapshot"))
    project_id: ProjectId
    commit_hash: str | None = None
    stack: dict[str, Any] = Field(default_factory=dict)
    commands: dict[str, list[list[str]]] = Field(default_factory=dict)
    structure: dict[str, Any] = Field(default_factory=dict)
    clarifications: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    indexed_at: AwareDatetime = Field(default_factory=utc_now)

    @property
    def needs_clarification(self) -> bool:
        return bool(self.clarifications)

    def allowlisted_commands(self) -> tuple[tuple[str, ...], ...]:
        """Discovered format/lint/type-check/test/build commands (PERMISSIONS: P1)."""
        kinds = ("test", "lint", "format", "typecheck", "build")
        return tuple(tuple(c) for k in kinds for c in self.commands.get(k, []) if c)


class Application(Entity):
    """A discovered, supported installed application (FR-003)."""

    id: ApplicationId = Field(default_factory=lambda: new_id("application"))
    key: NonEmptyStr  # catalog key, e.g. "jetbrains.pycharm"
    name: NonEmptyStr
    kind: NonEmptyStr  # "ide", "editor" or "browser"
    executable: NonEmptyStr
    version: str | None = None
    source: NonEmptyStr  # where it was discovered (registry, app-paths, programs-dir...)
    aliases: list[str] = Field(default_factory=list)
    available: bool = True
    discovered_at: AwareDatetime = Field(default_factory=utc_now)


class UserPreference(Entity):
    id: PreferenceId = Field(default_factory=lambda: new_id("preference"))
    key: NonEmptyStr
    value: Any = None
    scope: NonEmptyStr = "user"
    updated_at: AwareDatetime = Field(default_factory=utc_now)


class Conversation(Entity):
    """A conversation with Panos (FR-002). ``privacy`` is *sticky* (ADR-008): once any
    LOCAL_ONLY content enters the conversation it stays LOCAL_ONLY for good."""

    id: ConversationId = Field(default_factory=lambda: new_id("conversation"))
    project_id: ProjectId | None = None
    title: str = ""
    privacy: PrivacyMode = PrivacyMode.LOCAL_ONLY
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)


class ConversationMessage(Entity):
    id: MessageId = Field(default_factory=lambda: new_id("message"))
    conversation_id: ConversationId
    role: Annotated[str, Field(pattern=r"^(user|assistant|system)$")]
    content: str
    privacy: PrivacyMode
    provider: str | None = None
    model: str | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)


class AIUsage(Entity):
    """One model call, metered for cost and audit (FR-023, NFR-OBS-002)."""

    id: UsageId = Field(default_factory=lambda: new_id("usage"))
    provider: NonEmptyStr
    model: NonEmptyStr
    locality: Annotated[str, Field(pattern=r"^(local|cloud)$")]
    task: NonEmptyStr
    privacy: PrivacyMode
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    project_id: ProjectId | None = None
    conversation_id: ConversationId | None = None
    job_id: JobId | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)
