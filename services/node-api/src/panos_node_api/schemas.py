"""API response models (part of the versioned contract, ADR-018)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    api_version: str
    node_id: str
    uptime_s: float
    database: Literal["ok"]


class NodeInfo(BaseModel):
    id: str
    name: str
    platform: str


class CapabilitiesResponse(BaseModel):
    api_version: str
    version: str
    node: NodeInfo
    features: list[str]
    tools: list[str]
    desktop: dict[str, str] = {}


class ApprovalView(BaseModel):
    id: str
    status: str
    action_type: str
    target: str
    summary: str
    payload_hash: str
    project_id: str | None
    job_id: str | None
    requested_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    decided_by: str | None


class ApprovalList(BaseModel):
    approvals: list[ApprovalView]


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=2000)


class DecisionResponse(BaseModel):
    approval: ApprovalView
    # Single-use token bound to the exact approved action (ADR-006); only on approval.
    token: str | None


class AuditEventView(BaseModel):
    id: str
    created_at: datetime
    actor: str
    event_type: str
    action: str
    policy_decision: str | None
    project_id: str | None
    job_id: str | None
    metadata: dict[str, Any]
    hash: str


class AuditList(BaseModel):
    events: list[AuditEventView]


class ChainVerificationView(BaseModel):
    ok: bool
    checked: int
    first_broken_seq: int | None
    detail: str | None


class ToolResultView(BaseModel):
    tool: str
    status: str
    action: str | None
    output: dict[str, Any] | None
    error: str | None
    approval_id: str | None


class ProjectView(BaseModel):
    id: str
    name: str
    path: str
    privacy_mode: str
    preferred_ide: str | None
    status: str
    created_at: datetime
    policy_overrides: dict[str, str]
    knowledge_enabled: bool


class ProjectList(BaseModel):
    projects: list[ProjectView]


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    name: str | None = Field(default=None, max_length=100)
    create: bool = False  # create a new folder in the workspace instead of registering
    preferred_ide: str | None = None


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_ide: str | None = None  # "" clears it
    privacy_mode: Literal["LOCAL_ONLY", "STANDARD"] | None = None
    confirm_privacy_change: bool = False


class PolicyOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str | None = Field(default=None, pattern=r"^[Pp][0-4]$")  # null clears


class SnapshotView(BaseModel):
    id: str
    project_id: str
    commit_hash: str | None
    stack: dict[str, Any]
    commands: dict[str, list[list[str]]]
    structure: dict[str, Any]
    clarifications: list[str]
    confidence: float
    needs_clarification: bool
    indexed_at: datetime


class IndexStatsView(BaseModel):
    files_seen: int
    indexed: int
    unchanged: int
    removed: int
    chunks: int


class SearchHitView(BaseModel):
    project_id: str
    uri: str
    start_line: int
    end_line: int
    snippet: str
    score: float
    privacy_mode: str
    content_hash: str
    indexed_at: str


class SearchResults(BaseModel):
    hits: list[SearchHitView]


class ProjectOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: str | None = None
    dry_run: bool = False


class OpenPreview(BaseModel):
    app: str
    name: str
    executable: str
    arguments: list[str]


class AppView(BaseModel):
    key: str
    name: str
    kind: str
    executable: str
    version: str | None
    source: str
    aliases: list[str]


class AppList(BaseModel):
    apps: list[AppView]


class AppLaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Literal["none", "project", "path", "url"] = "none"
    project: str | None = None
    path: str | None = None
    url: str | None = None


class IdePreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: str
