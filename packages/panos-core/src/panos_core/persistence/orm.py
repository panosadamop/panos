"""ORM table definitions. Kept separate from domain entities (ADR-004)."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, MetaData, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from panos_core.persistence.types import UTCDateTime

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ID = String(36)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class NodeRow(Base):
    __tablename__ = "node"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(64))
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    public_identity: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ProjectRow(Base):
    __tablename__ = "project"
    __table_args__ = (UniqueConstraint("node_id", "path"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(Text)
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"))
    privacy_mode: Mapped[str] = mapped_column(String(16))
    preferred_ide: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class JobRow(Base):
    __tablename__ = "job"
    __table_args__ = (Index("ix_job_status", "status"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("project.id"))
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"))
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    budget: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class JobStepRow(Base):
    __tablename__ = "job_step"
    __table_args__ = (UniqueConstraint("job_id", "sequence"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("job.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    agent_type: Mapped[str | None] = mapped_column(String(64))
    tool_name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ActionApprovalRow(Base):
    __tablename__ = "action_approval"
    __table_args__ = (Index("ix_action_approval_status", "status"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("project.id"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("job.id"))
    action_type: Mapped[str] = mapped_column(String(128))
    target: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    decided_by: Mapped[str | None] = mapped_column(String(255))
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AuditEventRow(Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        Index("ix_audit_event_created_at", "created_at"),
        Index("ix_audit_event_job_id", "job_id"),
    )

    # Autoincrement sequence gives a total append order for the M2 hash chain.
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(ID, unique=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"))
    project_id: Mapped[str | None] = mapped_column(String(36))
    job_id: Mapped[str | None] = mapped_column(String(36))
    actor: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(255))
    policy_decision: Mapped[str | None] = mapped_column(String(32))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    prev_hash: Mapped[str] = mapped_column(String(64), server_default="")
    hash: Mapped[str] = mapped_column(String(64), server_default="")


class CheckpointRow(Base):
    __tablename__ = "checkpoint"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("job.id"))
    type: Mapped[str] = mapped_column(String(32))
    reference: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
