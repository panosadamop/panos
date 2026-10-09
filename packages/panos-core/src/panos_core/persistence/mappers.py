"""Explicit mapping between domain entities and ORM rows."""

from panos_core.domain.enums import (
    ApprovalStatus,
    CheckpointType,
    JobStatus,
    NodeStatus,
    PolicyDecision,
    PrivacyMode,
    ProjectStatus,
    StepStatus,
)
from panos_core.domain.models import (
    ActionApproval,
    AuditEvent,
    Checkpoint,
    Job,
    JobBudget,
    JobStep,
    Node,
    Project,
)
from panos_core.persistence.orm import (
    ActionApprovalRow,
    AuditEventRow,
    CheckpointRow,
    JobRow,
    JobStepRow,
    NodeRow,
    ProjectRow,
)


def node_to_row(n: Node, row: NodeRow | None = None) -> NodeRow:
    row = row or NodeRow(id=n.id)
    row.name, row.platform, row.capabilities = n.name, n.platform, list(n.capabilities)
    row.public_identity, row.status, row.last_seen_at = n.public_identity, n.status, n.last_seen_at
    return row


def node_from_row(r: NodeRow) -> Node:
    return Node(
        id=r.id,
        name=r.name,
        platform=r.platform,
        capabilities=list(r.capabilities),
        public_identity=r.public_identity,
        status=NodeStatus(r.status),
        last_seen_at=r.last_seen_at,
    )


def project_to_row(p: Project, row: ProjectRow | None = None) -> ProjectRow:
    row = row or ProjectRow(id=p.id)
    row.name, row.path, row.node_id = p.name, p.path, p.node_id
    row.privacy_mode, row.preferred_ide = p.privacy_mode, p.preferred_ide
    row.status, row.created_at = p.status, p.created_at
    return row


def project_from_row(r: ProjectRow) -> Project:
    return Project(
        id=r.id,
        name=r.name,
        path=r.path,
        node_id=r.node_id,
        privacy_mode=PrivacyMode(r.privacy_mode),
        preferred_ide=r.preferred_ide,
        status=ProjectStatus(r.status),
        created_at=r.created_at,
    )


def job_to_row(j: Job, row: JobRow | None = None) -> JobRow:
    row = row or JobRow(id=j.id)
    row.project_id, row.node_id, row.objective = j.project_id, j.node_id, j.objective
    row.status, row.budget = j.status, j.budget.model_dump()
    row.created_at, row.started_at, row.completed_at = j.created_at, j.started_at, j.completed_at
    return row


def job_from_row(r: JobRow) -> Job:
    return Job(
        id=r.id,
        project_id=r.project_id,
        node_id=r.node_id,
        objective=r.objective,
        status=JobStatus(r.status),
        budget=JobBudget.model_validate(r.budget or {}),
        created_at=r.created_at,
        started_at=r.started_at,
        completed_at=r.completed_at,
    )


def step_to_row(s: JobStep, row: JobStepRow | None = None) -> JobStepRow:
    row = row or JobStepRow(id=s.id)
    row.job_id, row.sequence, row.agent_type, row.tool_name = (
        s.job_id,
        s.sequence,
        s.agent_type,
        s.tool_name,
    )
    row.status, row.input_summary, row.output_summary = s.status, s.input_summary, s.output_summary
    row.started_at, row.completed_at = s.started_at, s.completed_at
    return row


def step_from_row(r: JobStepRow) -> JobStep:
    return JobStep(
        id=r.id,
        job_id=r.job_id,
        sequence=r.sequence,
        agent_type=r.agent_type,
        tool_name=r.tool_name,
        status=StepStatus(r.status),
        input_summary=r.input_summary,
        output_summary=r.output_summary,
        started_at=r.started_at,
        completed_at=r.completed_at,
    )


def approval_to_row(a: ActionApproval, row: ActionApprovalRow | None = None) -> ActionApprovalRow:
    row = row or ActionApprovalRow(id=a.id)
    row.project_id, row.job_id, row.action_type, row.target = (
        a.project_id,
        a.job_id,
        a.action_type,
        a.target,
    )
    row.payload_hash, row.summary, row.status, row.decided_by = (
        a.payload_hash,
        a.summary,
        a.status,
        a.decided_by,
    )
    row.requested_at, row.decided_at, row.expires_at, row.consumed_at = (
        a.requested_at,
        a.decided_at,
        a.expires_at,
        a.consumed_at,
    )
    return row


def approval_from_row(r: ActionApprovalRow) -> ActionApproval:
    return ActionApproval(
        id=r.id,
        project_id=r.project_id,
        job_id=r.job_id,
        action_type=r.action_type,
        target=r.target,
        payload_hash=r.payload_hash,
        summary=r.summary,
        status=ApprovalStatus(r.status),
        decided_by=r.decided_by,
        requested_at=r.requested_at,
        decided_at=r.decided_at,
        expires_at=r.expires_at,
        consumed_at=r.consumed_at,
    )


def audit_to_row(e: AuditEvent) -> AuditEventRow:
    return AuditEventRow(
        id=e.id,
        node_id=e.node_id,
        project_id=e.project_id,
        job_id=e.job_id,
        actor=e.actor,
        event_type=e.event_type,
        action=e.action,
        policy_decision=e.policy_decision,
        metadata_json=dict(e.metadata),
        created_at=e.created_at,
        prev_hash=e.prev_hash,
        hash=e.hash,
    )


def audit_from_row(r: AuditEventRow) -> AuditEvent:
    return AuditEvent(
        id=r.id,
        node_id=r.node_id,
        project_id=r.project_id,
        job_id=r.job_id,
        actor=r.actor,
        event_type=r.event_type,
        action=r.action,
        policy_decision=PolicyDecision(r.policy_decision) if r.policy_decision else None,
        metadata=dict(r.metadata_json or {}),
        created_at=r.created_at,
        prev_hash=r.prev_hash,
        hash=r.hash,
    )


def checkpoint_to_row(c: Checkpoint) -> CheckpointRow:
    return CheckpointRow(
        id=c.id,
        project_id=c.project_id,
        job_id=c.job_id,
        type=c.type,
        reference=c.reference,
        created_at=c.created_at,
    )


def checkpoint_from_row(r: CheckpointRow) -> Checkpoint:
    return Checkpoint(
        id=r.id,
        project_id=r.project_id,
        job_id=r.job_id,
        type=CheckpointType(r.type),
        reference=r.reference,
        created_at=r.created_at,
    )
