"""Hub tables (ADR-028 §7: only non-sensitive metadata is persisted)."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSON,
        list[Any]: JSON,
    }


class HubUser(Base):
    __tablename__ = "hub_user"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime]


class Passkey(Base):
    __tablename__ = "passkey"
    id: Mapped[str] = mapped_column(String(512), primary_key=True)  # credential id, base64url
    user_id: Mapped[str] = mapped_column(ForeignKey("hub_user.id", ondelete="CASCADE"), index=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(BigInteger, default=0)
    label: Mapped[str] = mapped_column(String(128), default="")
    transports: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime]
    last_used_at: Mapped[datetime | None]


class HubSession(Base):
    __tablename__ = "hub_session"
    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)  # SHA-256 of the cookie
    user_id: Mapped[str] = mapped_column(ForeignKey("hub_user.id", ondelete="CASCADE"))
    csrf: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class SetupCode(Base):
    """One-time codes: ``enroll`` (a node joins) and ``passkey`` (first dashboard sign-in)."""

    __tablename__ = "setup_code"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    used_by: Mapped[str | None] = mapped_column(String(64))


class NodeRow(Base):
    __tablename__ = "node"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    platform: Mapped[str] = mapped_column(String(32), default="")
    version: Mapped[str] = mapped_column(String(32), default="")
    enrolled_at: Mapped[datetime]
    last_seen_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    tools: Mapped[list[Any]] = mapped_column(default=list)
    features: Mapped[list[Any]] = mapped_column(default=list)
    projects: Mapped[list[Any]] = mapped_column(default=list)
    running_jobs: Mapped[int] = mapped_column(Integer, default=0)
    accepts_remote_approvals: Mapped[bool] = mapped_column(Boolean, default=True)


class NodeCert(Base):
    __tablename__ = "node_cert"
    serial: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id", ondelete="CASCADE"), index=True)
    issued_at: Mapped[datetime]
    not_after: Mapped[datetime]
    retired_at: Mapped[datetime | None]


class DispatchRow(Base):
    __tablename__ = "dispatch"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id", ondelete="CASCADE"), index=True)
    project: Mapped[str | None] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    objective: Mapped[str] = mapped_column(Text)
    requested_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime]
    state: Mapped[str] = mapped_column(String(16))  # sent | accepted | refused | failed
    job_id: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)


class JobMirror(Base):
    __tablename__ = "job_mirror"
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    status_reason: Mapped[str | None] = mapped_column(Text)
    phase: Mapped[str | None] = mapped_column(String(32))
    project_id: Mapped[str | None] = mapped_column(String(64))
    project_name: Mapped[str | None] = mapped_column(String(128))
    privacy_mode: Mapped[str] = mapped_column(String(16))
    dispatch_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime]
    completed_at: Mapped[datetime | None]
    updated_at: Mapped[datetime]
    steps: Mapped[list[Any]] = mapped_column(default=list)  # summaries removed for LOCAL_ONLY


class ApprovalMirror(Base):
    __tablename__ = "approval_mirror"
    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(64))
    project_id: Mapped[str | None] = mapped_column(String(64))
    action_type: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)  # None for LOCAL_ONLY (fetched live)
    status: Mapped[str] = mapped_column(String(16), index=True)
    privacy_mode: Mapped[str] = mapped_column(String(16))
    requested_at: Mapped[datetime]
    expires_at: Mapped[datetime | None]
    updated_at: Mapped[datetime]


class HubAudit(Base):
    __tablename__ = "hub_audit"
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime]
    actor: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    node_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
