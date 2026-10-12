"""Hub ↔ node message protocol (ADR-028 §4, AC-US062-02).

Every frame is an :class:`Envelope` whose ``type`` must be in the allowlist for its
direction; the payload is validated against that type's model. There is deliberately no
message that reads files, runs tools or commands, or changes settings: the hub can only
dispatch a job, control a job, relay an approval decision, or ask a read-only query.
"""

import json
import secrets
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROTOCOL_VERSION: Final = 1
MAX_FRAME_BYTES: Final = 4_000_000


class ProtocolError(ValueError):
    """A frame is malformed, too large, of an unknown type or the wrong direction."""


class _Msg(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ===================================================================== node → hub
class Hello(_Msg):
    node_id: str
    name: str = Field(max_length=128)
    platform: str = Field(max_length=32)
    version: str = Field(max_length=32)
    api_version: str = Field(max_length=8)
    tools: list[str] = Field(default_factory=list, max_length=500)
    features: list[str] = Field(default_factory=list, max_length=100)
    accepts_remote_approvals: bool = True


class Heartbeat(_Msg):
    running_jobs: int = Field(ge=0)


class ProjectInfo(_Msg):
    id: str
    name: str = Field(max_length=128)
    privacy_mode: str
    technologies: list[str] = Field(default_factory=list, max_length=50)


class Inventory(_Msg):
    projects: list[ProjectInfo] = Field(max_length=1000)


class StepInfo(_Msg):
    sequence: int
    agent: str | None = None
    tool: str | None = None
    status: str
    summary: str | None = Field(default=None, max_length=2000)  # None for LOCAL_ONLY projects


class JobUpdate(_Msg):
    job_id: str
    kind: str
    objective: str = Field(max_length=10_000)
    status: str
    status_reason: str | None = Field(default=None, max_length=2000)
    phase: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    privacy_mode: str
    dispatch_id: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    steps: list[StepInfo] = Field(default_factory=list, max_length=500)


class ApprovalUpdate(_Msg):
    approval_id: str
    job_id: str | None = None
    project_id: str | None = None
    action_type: str
    target: str = Field(max_length=2000)
    payload_hash: str
    summary: str | None = Field(default=None, max_length=10_000)  # None for LOCAL_ONLY
    status: str
    privacy_mode: str
    requested_at: datetime
    expires_at: datetime | None = None


class Renew(_Msg):
    """Certificate renewal over the existing link (ADR-029 §6); answered with a Reply."""

    csr: str = Field(min_length=100, max_length=8000)


class Reply(_Msg):
    to: str  # envelope id of the request
    ok: bool
    data: Any = None
    error: str | None = Field(default=None, max_length=2000)


# ===================================================================== hub → node
class Dispatch(_Msg):
    dispatch_id: str
    objective: str = Field(min_length=3, max_length=10_000)
    project: str | None = Field(default=None, max_length=128, description="name or id on the node")
    kind: Literal["coding", "command", "research"] = "coding"
    argv: list[str] | None = Field(default=None, max_length=256)
    urls: list[str] | None = Field(default=None, max_length=10)
    requested_by: str = Field(max_length=200)


class Control(_Msg):
    job_id: str
    action: Literal["pause", "resume", "cancel"]
    requested_by: str = Field(max_length=200)


class Decision(_Msg):
    approval_id: str
    approve: bool
    comment: str = Field(default="", max_length=2000)
    decided_by: str = Field(max_length=200)


QueryName = Literal["job", "approval", "audit", "usage", "traceability", "projects"]


class Query(_Msg):
    name: QueryName
    params: dict[str, str | int | None] = Field(default_factory=dict)


NODE_TO_HUB: Final[dict[str, type[_Msg]]] = {
    "hello": Hello,
    "heartbeat": Heartbeat,
    "inventory": Inventory,
    "job": JobUpdate,
    "approval": ApprovalUpdate,
    "renew": Renew,
    "reply": Reply,
}
HUB_TO_NODE: Final[dict[str, type[_Msg]]] = {
    "dispatch": Dispatch,
    "control": Control,
    "decision": Decision,
    "query": Query,
    "reply": Reply,  # acknowledgements (e.g. to hello)
}
_TYPE_OF: Final = {
    **{v: k for k, v in NODE_TO_HUB.items()},
    **{v: k for k, v in HUB_TO_NODE.items()},
}

Direction = Literal["to_hub", "to_node"]


class Envelope(_Msg):
    v: int = PROTOCOL_VERSION
    id: str = Field(default_factory=lambda: secrets.token_hex(8))
    type: str
    payload: dict[str, Any]


def encode(message: _Msg, *, msg_id: str | None = None) -> str:
    env = Envelope(type=_TYPE_OF[type(message)], payload=message.model_dump(mode="json"))
    if msg_id:
        env.id = msg_id
    return env.model_dump_json()


def decode(frame: str | bytes, direction: Direction) -> tuple[Envelope, _Msg]:
    """Parse and validate one frame for ``direction``; raise :class:`ProtocolError`."""
    if len(frame) > MAX_FRAME_BYTES:
        raise ProtocolError("frame too large")
    try:
        env = Envelope.model_validate(json.loads(frame))
    except (ValueError, ValidationError) as exc:
        raise ProtocolError("malformed frame") from exc
    if env.v != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version {env.v}")
    allowed = NODE_TO_HUB if direction == "to_hub" else HUB_TO_NODE
    model = allowed.get(env.type)
    if model is None:
        raise ProtocolError(f"message type {env.type!r} is not allowed {direction}")
    try:
        return env, model.model_validate(env.payload)
    except ValidationError as exc:
        raise ProtocolError(f"invalid {env.type} payload: {exc.errors()[0]['msg']}") from exc
