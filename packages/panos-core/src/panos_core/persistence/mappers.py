"""Explicit mapping between domain entities and ORM rows."""

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
from panos_core.domain.models import (
    ActionApproval,
    Application,
    AuditEvent,
    Checkpoint,
    Job,
    JobBudget,
    JobStep,
    Node,
    Project,
    RepositorySnapshot,
    UserPreference,
)
from panos_core.persistence.orm import (
    ActionApprovalRow,
    ApplicationRow,
    AuditEventRow,
    CheckpointRow,
    JobRow,
    JobStepRow,
    NodeRow,
    ProjectRow,
    RepositorySnapshotRow,
    UserPreferenceRow,
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
    row.policy_overrides = {k: int(v) for k, v in p.policy_overrides.items()}
    row.ai_policy, row.knowledge_enabled = dict(p.ai_policy), p.knowledge_enabled
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
        policy_overrides={k: PermissionLevel(v) for k, v in (r.policy_overrides or {}).items()},
        ai_policy=dict(r.ai_policy or {}),
        knowledge_enabled=bool(r.knowledge_enabled),
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


def snapshot_to_row(s: RepositorySnapshot) -> RepositorySnapshotRow:
    return RepositorySnapshotRow(
        id=s.id,
        project_id=s.project_id,
        commit_hash=s.commit_hash,
        stack_json=s.stack,
        commands_json=s.commands,
        structure_json=s.structure,
        clarifications_json=list(s.clarifications),
        confidence=s.confidence,
        indexed_at=s.indexed_at,
    )


def snapshot_from_row(r: RepositorySnapshotRow) -> RepositorySnapshot:
    return RepositorySnapshot(
        id=r.id,
        project_id=r.project_id,
        commit_hash=r.commit_hash,
        stack=dict(r.stack_json or {}),
        commands={k: [list(c) for c in v] for k, v in (r.commands_json or {}).items()},
        structure=dict(r.structure_json or {}),
        clarifications=list(r.clarifications_json or []),
        confidence=r.confidence,
        indexed_at=r.indexed_at,
    )


def application_to_row(a: Application, row: ApplicationRow | None = None) -> ApplicationRow:
    row = row or ApplicationRow(id=a.id)
    row.key, row.name, row.kind, row.executable = a.key, a.name, a.kind, a.executable
    row.version, row.source, row.aliases = a.version, a.source, list(a.aliases)
    row.available, row.discovered_at = a.available, a.discovered_at
    return row


def application_from_row(r: ApplicationRow) -> Application:
    return Application(
        id=r.id,
        key=r.key,
        name=r.name,
        kind=r.kind,
        executable=r.executable,
        version=r.version,
        source=r.source,
        aliases=list(r.aliases or []),
        available=r.available,
        discovered_at=r.discovered_at,
    )


def preference_from_row(r: UserPreferenceRow) -> UserPreference:
    return UserPreference(id=r.id, key=r.key, value=r.value, scope=r.scope, updated_at=r.updated_at)
