"""ORM table definitions. Kept separate from domain entities (ADR-004)."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
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
    policy_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, server_default="{}")
    ai_policy: Mapped[dict[str, Any]] = mapped_column(JSON, server_default="{}")
    knowledge_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))


class JobRow(Base):
    __tablename__ = "job"
    __table_args__ = (Index("ix_job_status", "status"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("project.id"))
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"))
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    budget: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    kind: Mapped[str] = mapped_column(String(64), default="coding")
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status_reason: Mapped[str] = mapped_column(Text, default="")
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
    __table_args__ = (Index("ix_checkpoint_job_id", "job_id"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("job.id"))
    type: Mapped[str] = mapped_column(String(32))
    reference: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class RepositorySnapshotRow(Base):
    __tablename__ = "repository_snapshot"
    __table_args__ = (Index("ix_repository_snapshot_project", "project_id", "indexed_at"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    commit_hash: Mapped[str | None] = mapped_column(String(64))
    stack_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    commands_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    structure_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    clarifications_json: Mapped[list[str]] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    indexed_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ApplicationRow(Base):
    __tablename__ = "application"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32))
    executable: Mapped[str] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(64))
    aliases: Mapped[list[str]] = mapped_column(JSON)
    available: Mapped[bool] = mapped_column(Boolean)
    discovered_at: Mapped[datetime] = mapped_column(UTCDateTime)


class UserPreferenceRow(Base):
    __tablename__ = "user_preference"
    __table_args__ = (UniqueConstraint("scope", "key"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    key: Mapped[str] = mapped_column(String(255))
    value: Mapped[Any] = mapped_column(JSON)
    scope: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ConversationRow(Base):
    __tablename__ = "conversation"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("project.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(Text)
    privacy: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ConversationMessageRow(Base):
    __tablename__ = "conversation_message"
    __table_args__ = (Index("ix_conversation_message_conv", "conversation_id", "created_at"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    privacy: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class AIUsageRow(Base):
    __tablename__ = "ai_usage"
    __table_args__ = (Index("ix_ai_usage_created_at", "created_at"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    locality: Mapped[str] = mapped_column(String(8))
    task: Mapped[str] = mapped_column(String(32))
    privacy: Mapped[str] = mapped_column(String(16))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[int] = mapped_column(Integer)
    project_id: Mapped[str | None] = mapped_column(String(36))
    conversation_id: Mapped[str | None] = mapped_column(String(36))
    job_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


# ---------------------------------------------------------------- requirements (M6, ADR-014)
class RequirementRow(Base):
    __tablename__ = "requirement"
    __table_args__ = (
        UniqueConstraint("project_id", "key", "version"),
        Index("ix_requirement_project_key", "project_id", "key"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    parent_key: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24))
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class RequirementApprovalRow(Base):
    __tablename__ = "requirement_approval"
    __table_args__ = (Index("ix_requirement_approval_key", "project_id", "key"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirement.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(24))
    decided_by: Mapped[str] = mapped_column(String(128))
    comments: Mapped[str] = mapped_column(Text, default="")
    cascaded_from_id: Mapped[str | None] = mapped_column(ID)
    excluded_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime)


class TraceLinkRow(Base):
    __tablename__ = "trace_link"
    __table_args__ = (
        UniqueConstraint("project_id", "source_key", "target", "relation"),
        Index("ix_trace_link_target", "project_id", "target"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    source_key: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(600))
    relation: Mapped[str] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class InterviewRow(Base):
    __tablename__ = "interview"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    brief: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    ambiguities: Mapped[list[str]] = mapped_column(JSON, default=list)
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    explicit_stack: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class GeneratedSkillRow(Base):
    __tablename__ = "generated_skill"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    facts_hash: Mapped[str] = mapped_column(String(64))
    source_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    path: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ConnectorAccountRow(Base):
    __tablename__ = "connector_account"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(256))
    base_url: Mapped[str | None] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    privacy_mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class KnowledgeCollectionRow(Base):
    __tablename__ = "knowledge_collection"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    location: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    connector_id: Mapped[str | None] = mapped_column(
        ForeignKey("connector_account.id", ondelete="SET NULL")
    )
    privacy_mode: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_indexed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# ---------------------------------------------------------------------- effort (M10, ADR-031)
class EstimateBaselineRow(Base):
    __tablename__ = "estimate_baseline"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    technology: Mapped[str] = mapped_column(String(64))
    hours_per_point: Mapped[float] = mapped_column(Float)
    calibration_id: Mapped[str | None] = mapped_column(ID)
    stories: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class EffortActualRow(Base):
    __tablename__ = "effort_actual"
    __table_args__ = (
        UniqueConstraint("source", "external_ref"),
        Index("ix_effort_actual_project_key", "project_id", "requirement_key"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    requirement_key: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16))
    hours: Mapped[float] = mapped_column(Float)
    role: Mapped[str] = mapped_column(String(32))
    work_date: Mapped[date] = mapped_column(Date)
    external_ref: Mapped[str | None] = mapped_column(String(256))
    machine: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    completes: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), index=True)
    logged_by: Mapped[str] = mapped_column(String(200))
    decided_by: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class CalibrationRow(Base):
    __tablename__ = "calibration"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    scope: Mapped[str] = mapped_column(String(96), index=True)
    factor: Mapped[float] = mapped_column(Float)
    hours_per_point: Mapped[float] = mapped_column(Float)
    base_hours_per_point: Mapped[float] = mapped_column(Float)
    stories: Mapped[int] = mapped_column(Integer)
    spread: Mapped[float] = mapped_column(Float)
    bias: Mapped[float] = mapped_column(Float)
    mape: Mapped[float] = mapped_column(Float)
    basis: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    decided_by: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
