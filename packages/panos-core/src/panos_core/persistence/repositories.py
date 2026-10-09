"""SQLAlchemy implementations of the domain repository ports."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from panos_core.domain.clock import utc_now
from panos_core.domain.enums import ApprovalStatus, PrivacyMode
from panos_core.domain.errors import EntityNotFoundError
from panos_core.domain.models import (
    ActionApproval,
    AIUsage,
    Application,
    AuditEvent,
    Checkpoint,
    Conversation,
    ConversationMessage,
    Job,
    JobStep,
    Node,
    Project,
    RepositorySnapshot,
    UserPreference,
)
from panos_core.persistence import mappers as m
from panos_core.persistence.orm import (
    ActionApprovalRow,
    AIUsageRow,
    ApplicationRow,
    AuditEventRow,
    CheckpointRow,
    ConversationMessageRow,
    ConversationRow,
    JobRow,
    JobStepRow,
    NodeRow,
    ProjectRow,
    RepositorySnapshotRow,
    UserPreferenceRow,
)


class _SqlRepository:
    """Base for repositories. ``add`` flushes immediately so inserts follow call order;
    without ORM relationships SQLAlchemy does not order inserts by foreign keys."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session


class SqlNodeRepository(_SqlRepository):
    async def add(self, node: Node) -> None:
        self._session.add(m.node_to_row(node))
        await self._session.flush()  # keep insert order = call order (FK safety)

    async def get(self, node_id: str) -> Node | None:
        row = await self._session.get(NodeRow, node_id)
        return m.node_from_row(row) if row else None

    async def first(self) -> Node | None:
        row = (await self._session.scalars(select(NodeRow).limit(1))).first()
        return m.node_from_row(row) if row else None

    async def update(self, node: Node) -> None:
        row = await self._session.get(NodeRow, node.id)
        if row is None:
            raise EntityNotFoundError("node", node.id)
        m.node_to_row(node, row)


class SqlProjectRepository(_SqlRepository):
    async def add(self, project: Project) -> None:
        self._session.add(m.project_to_row(project))
        await self._session.flush()  # keep insert order = call order (FK safety)

    async def get(self, project_id: str) -> Project | None:
        row = await self._session.get(ProjectRow, project_id)
        return m.project_from_row(row) if row else None

    async def get_by_path(self, path: str) -> Project | None:
        stmt = select(ProjectRow).where(ProjectRow.path == path)
        row = (await self._session.scalars(stmt)).first()
        return m.project_from_row(row) if row else None

    async def get_by_name(self, name: str) -> Project | None:
        stmt = select(ProjectRow).where(ProjectRow.name == name)
        row = (await self._session.scalars(stmt)).first()
        return m.project_from_row(row) if row else None

    async def list(self) -> Sequence[Project]:
        rows = await self._session.scalars(select(ProjectRow).order_by(ProjectRow.created_at))
        return [m.project_from_row(r) for r in rows]

    async def update(self, project: Project) -> None:
        row = await self._session.get(ProjectRow, project.id)
        if row is None:
            raise EntityNotFoundError("project", project.id)
        m.project_to_row(project, row)


class SqlJobRepository(_SqlRepository):
    async def add(self, job: Job) -> None:
        self._session.add(m.job_to_row(job))
        await self._session.flush()  # keep insert order = call order (FK safety)

    async def get(self, job_id: str) -> Job | None:
        row = await self._session.get(JobRow, job_id)
        return m.job_from_row(row) if row else None

    async def update(self, job: Job) -> None:
        row = await self._session.get(JobRow, job.id)
        if row is None:
            raise EntityNotFoundError("job", job.id)
        m.job_to_row(job, row)

    async def add_step(self, step: JobStep) -> None:
        self._session.add(m.step_to_row(step))
        await self._session.flush()  # keep insert order = call order (FK safety)

    async def update_step(self, step: JobStep) -> None:
        row = await self._session.get(JobStepRow, step.id)
        if row is None:
            raise EntityNotFoundError("job step", step.id)
        m.step_to_row(step, row)

    async def list_steps(self, job_id: str) -> Sequence[JobStep]:
        stmt = select(JobStepRow).where(JobStepRow.job_id == job_id).order_by(JobStepRow.sequence)
        return [m.step_from_row(r) for r in await self._session.scalars(stmt)]

    async def list(
        self, *, statuses: Sequence[str] | None = None, limit: int = 100
    ) -> Sequence[Job]:
        stmt = select(JobRow).order_by(JobRow.created_at.desc()).limit(limit)
        if statuses:
            stmt = stmt.where(JobRow.status.in_(list(statuses)))
        return [m.job_from_row(r) for r in await self._session.scalars(stmt)]


class SqlApprovalRepository(_SqlRepository):
    async def add(self, approval: ActionApproval) -> None:
        self._session.add(m.approval_to_row(approval))
        await self._session.flush()  # keep insert order = call order (FK safety)

    async def get(self, approval_id: str) -> ActionApproval | None:
        row = await self._session.get(ActionApprovalRow, approval_id)
        return m.approval_from_row(row) if row else None

    async def list_by_status(self, status: ApprovalStatus) -> Sequence[ActionApproval]:
        stmt = (
            select(ActionApprovalRow)
            .where(ActionApprovalRow.status == status)
            .order_by(ActionApprovalRow.requested_at)
        )
        return [m.approval_from_row(r) for r in await self._session.scalars(stmt)]

    async def update(self, approval: ActionApproval) -> None:
        row = await self._session.get(ActionApprovalRow, approval.id)
        if row is None:
            raise EntityNotFoundError("approval", approval.id)
        m.approval_to_row(approval, row)

    async def find_pending(self, job_id: str | None, payload_hash: str) -> ActionApproval | None:
        stmt = select(ActionApprovalRow).where(
            ActionApprovalRow.status == ApprovalStatus.PENDING,
            ActionApprovalRow.payload_hash == payload_hash,
            ActionApprovalRow.job_id.is_(None)
            if job_id is None
            else ActionApprovalRow.job_id == job_id,
        )
        row = (await self._session.scalars(stmt)).first()
        return m.approval_from_row(row) if row else None

    async def consume(self, approval_id: str, payload_hash: str, consumed_at: datetime) -> bool:
        """Atomically mark an APPROVED approval as CONSUMED. Returns False if it was not
        APPROVED (already consumed, rejected, expired...) or the hash does not match."""
        stmt = (
            update(ActionApprovalRow)
            .where(
                ActionApprovalRow.id == approval_id,
                ActionApprovalRow.status == ApprovalStatus.APPROVED,
                ActionApprovalRow.payload_hash == payload_hash,
            )
            .values(status=ApprovalStatus.CONSUMED, consumed_at=consumed_at)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        return getattr(result, "rowcount", 0) == 1


class SqlAuditRepository(_SqlRepository):
    """Append-only: there is deliberately no update or delete."""

    async def append(self, event: AuditEvent) -> None:
        self._session.add(m.audit_to_row(event))
        await self._session.flush()  # keep insert order = call order (FK safety)

    async def list(
        self,
        *,
        project_id: str | None = None,
        job_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> Sequence[AuditEvent]:
        stmt = select(AuditEventRow)
        if project_id is not None:
            stmt = stmt.where(AuditEventRow.project_id == project_id)
        if job_id is not None:
            stmt = stmt.where(AuditEventRow.job_id == job_id)
        if event_type is not None:
            stmt = stmt.where(AuditEventRow.event_type == event_type)
        stmt = stmt.order_by(AuditEventRow.seq.desc()).limit(max(1, min(limit, 1000)))
        return [m.audit_from_row(r) for r in await self._session.scalars(stmt)]

    async def last_hash(self) -> str:
        stmt = select(AuditEventRow.hash).order_by(AuditEventRow.seq.desc()).limit(1)
        value = (await self._session.scalars(stmt)).first()
        return value or ""

    async def list_in_order(self) -> Sequence[tuple[int, AuditEvent]]:
        """All events in append order, with their sequence numbers (for chain verification)."""
        rows = await self._session.scalars(select(AuditEventRow).order_by(AuditEventRow.seq))
        return [(r.seq, m.audit_from_row(r)) for r in rows]


class SqlCheckpointRepository(_SqlRepository):
    async def add(self, checkpoint: Checkpoint) -> None:
        self._session.add(m.checkpoint_to_row(checkpoint))
        await self._session.flush()  # keep insert order = call order (FK safety)

    async def list_for_project(self, project_id: str) -> Sequence[Checkpoint]:
        stmt = (
            select(CheckpointRow)
            .where(CheckpointRow.project_id == project_id)
            .order_by(CheckpointRow.created_at)
        )
        return [m.checkpoint_from_row(r) for r in await self._session.scalars(stmt)]

    async def get(self, checkpoint_id: str) -> Checkpoint | None:
        row = await self._session.get(CheckpointRow, checkpoint_id)
        return m.checkpoint_from_row(row) if row else None

    async def list_for_job(self, job_id: str) -> Sequence[Checkpoint]:
        stmt = (
            select(CheckpointRow)
            .where(CheckpointRow.job_id == job_id)
            .order_by(CheckpointRow.created_at)
        )
        return [m.checkpoint_from_row(r) for r in await self._session.scalars(stmt)]


class SqlSnapshotRepository(_SqlRepository):
    async def add(self, snapshot: RepositorySnapshot) -> None:
        self._session.add(m.snapshot_to_row(snapshot))
        await self._session.flush()  # keep insert order = call order (FK safety)

    async def latest(self, project_id: str) -> RepositorySnapshot | None:
        stmt = (
            select(RepositorySnapshotRow)
            .where(RepositorySnapshotRow.project_id == project_id)
            .order_by(RepositorySnapshotRow.indexed_at.desc())
            .limit(1)
        )
        row = (await self._session.scalars(stmt)).first()
        return m.snapshot_from_row(row) if row else None


class SqlApplicationRepository(_SqlRepository):
    async def list(self, *, available_only: bool = True) -> Sequence[Application]:
        stmt = select(ApplicationRow).order_by(ApplicationRow.kind, ApplicationRow.name)
        if available_only:
            stmt = stmt.where(ApplicationRow.available.is_(True))
        return [m.application_from_row(r) for r in await self._session.scalars(stmt)]

    async def get_by_key(self, key: str) -> Application | None:
        stmt = select(ApplicationRow).where(ApplicationRow.key == key)
        row = (await self._session.scalars(stmt)).first()
        return m.application_from_row(row) if row else None

    async def upsert(self, app: Application) -> Application:
        stmt = select(ApplicationRow).where(ApplicationRow.key == app.key)
        row = (await self._session.scalars(stmt)).first()
        if row is None:
            self._session.add(m.application_to_row(app))
            await self._session.flush()
            return app
        app.id = row.id
        m.application_to_row(app, row)
        return app

    async def mark_unavailable_except(self, keys: set[str]) -> None:
        for row in await self._session.scalars(select(ApplicationRow)):
            if row.key not in keys:
                row.available = False


class SqlPreferenceRepository(_SqlRepository):
    async def get(self, key: str, scope: str = "user") -> UserPreference | None:
        stmt = select(UserPreferenceRow).where(
            UserPreferenceRow.key == key, UserPreferenceRow.scope == scope
        )
        row = (await self._session.scalars(stmt)).first()
        return m.preference_from_row(row) if row else None

    async def set(self, key: str, value: object, scope: str = "user") -> UserPreference:
        stmt = select(UserPreferenceRow).where(
            UserPreferenceRow.key == key, UserPreferenceRow.scope == scope
        )
        row = (await self._session.scalars(stmt)).first()
        if row is None:
            pref = UserPreference(key=key, value=value, scope=scope)
            self._session.add(
                UserPreferenceRow(
                    id=pref.id, key=key, value=value, scope=scope, updated_at=pref.updated_at
                )
            )
            await self._session.flush()
            return pref
        row.value, row.updated_at = value, utc_now()
        return m.preference_from_row(row)

    async def list(self, scope: str = "user") -> Sequence[UserPreference]:
        stmt = select(UserPreferenceRow).where(UserPreferenceRow.scope == scope)
        return [m.preference_from_row(r) for r in await self._session.scalars(stmt)]


class SqlConversationRepository(_SqlRepository):
    async def add(self, conversation: Conversation) -> None:
        self._session.add(ConversationRow(**conversation.model_dump()))
        await self._session.flush()

    async def get(self, conversation_id: str) -> Conversation | None:
        row = await self._session.get(ConversationRow, conversation_id)
        return _conversation(row) if row else None

    async def update(self, conversation: Conversation) -> None:
        row = await self._session.get(ConversationRow, conversation.id)
        if row is None:
            raise EntityNotFoundError("conversation", conversation.id)
        for key, value in conversation.model_dump().items():
            setattr(row, key, value)

    async def list(self, *, limit: int = 50) -> Sequence[Conversation]:
        stmt = select(ConversationRow).order_by(ConversationRow.updated_at.desc()).limit(limit)
        return [_conversation(r) for r in await self._session.scalars(stmt)]

    async def add_message(self, message: ConversationMessage) -> None:
        self._session.add(ConversationMessageRow(**message.model_dump()))
        await self._session.flush()

    async def messages(
        self, conversation_id: str, *, limit: int = 50
    ) -> Sequence[ConversationMessage]:
        stmt = (
            select(ConversationMessageRow)
            .where(ConversationMessageRow.conversation_id == conversation_id)
            .order_by(ConversationMessageRow.created_at.desc())
            .limit(limit)
        )
        rows = list(await self._session.scalars(stmt))
        return [_message(r) for r in reversed(rows)]


def _conversation(r: ConversationRow) -> Conversation:
    return Conversation(
        id=r.id,
        project_id=r.project_id,
        title=r.title,
        privacy=PrivacyMode(r.privacy),
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _message(r: ConversationMessageRow) -> ConversationMessage:
    return ConversationMessage(
        id=r.id,
        conversation_id=r.conversation_id,
        role=r.role,
        content=r.content,
        privacy=PrivacyMode(r.privacy),
        provider=r.provider,
        model=r.model,
        created_at=r.created_at,
    )


class SqlUsageRepository(_SqlRepository):
    async def add(self, usage: AIUsage) -> None:
        self._session.add(AIUsageRow(**usage.model_dump()))
        await self._session.flush()

    async def since(self, start: datetime) -> Sequence[AIUsage]:
        stmt = (
            select(AIUsageRow).where(AIUsageRow.created_at >= start).order_by(AIUsageRow.created_at)
        )
        return [
            AIUsage(**{c: getattr(r, c) for c in AIUsage.model_fields})
            for r in await self._session.scalars(stmt)
        ]

    async def totals_for_job(self, job_id: str) -> tuple[int, float]:
        """(input + output tokens, cost USD) of every model call made for a job."""
        stmt = select(
            func.coalesce(func.sum(AIUsageRow.input_tokens + AIUsageRow.output_tokens), 0),
            func.coalesce(func.sum(AIUsageRow.cost_usd), 0.0),
        ).where(AIUsageRow.job_id == job_id)
        tokens, cost = (await self._session.execute(stmt)).one()
        return int(tokens), float(cost)

    async def cost_since(self, start: datetime) -> float:
        stmt = select(func.coalesce(func.sum(AIUsageRow.cost_usd), 0.0)).where(
            AIUsageRow.created_at >= start
        )
        return float((await self._session.execute(stmt)).scalar_one())
