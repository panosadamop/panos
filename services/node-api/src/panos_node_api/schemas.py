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
