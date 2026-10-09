"""Domain enumerations."""

from enum import IntEnum, StrEnum


class PrivacyMode(StrEnum):
    """Project/content privacy classification (BR-004, ADR-008, ADR-024).

    ``LOCAL_ONLY`` content must never reach a cloud provider.
    """

    LOCAL_ONLY = "LOCAL_ONLY"
    STANDARD = "STANDARD"


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class JobStatus(StrEnum):
    """Job lifecycle states, exactly as listed in FR-022."""

    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING_FOR_TOOL = "WAITING_FOR_TOOL"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_JOB_STATES


_TERMINAL_JOB_STATES = frozenset({JobStatus.FAILED, JobStatus.COMPLETED, JobStatus.CANCELLED})


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class ApprovalStatus(StrEnum):
    """Tool-action approval states (ADR-006)."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CONSUMED = "CONSUMED"


class PermissionLevel(IntEnum):
    """Permission levels from PERMISSIONS.md. Higher value = more restrictive."""

    P0_READ = 0
    P1_SAFE_WRITE = 1
    P2_CONTROLLED = 2
    P3_APPROVAL_REQUIRED = 3
    P4_FORBIDDEN = 4


class PolicyDecision(StrEnum):
    """Outcome of a policy evaluation (ADR-005)."""

    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


class NodeStatus(StrEnum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    REVOKED = "REVOKED"


class CheckpointType(StrEnum):
    SHADOW_GIT = "SHADOW_GIT"
