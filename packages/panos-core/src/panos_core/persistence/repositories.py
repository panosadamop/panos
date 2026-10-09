"""SQLAlchemy implementations of the domain repository ports."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from panos_core.domain.enums import ApprovalStatus
from panos_core.domain.errors import EntityNotFoundError
from panos_core.domain.models import (
    ActionApproval,
    AuditEvent,
    Checkpoint,
    Job,
    JobStep,
    Node,
    Project,
)
from panos_core.persistence import mappers as m
from panos_core.persistence.orm import (
    ActionApprovalRow,
    AuditEventRow,
    CheckpointRow,
    JobRow,
    JobStepRow,
    NodeRow,
    ProjectRow,
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
