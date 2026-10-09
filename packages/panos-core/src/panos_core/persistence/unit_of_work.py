"""Unit of work: one transaction exposing all repositories."""

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from panos_core.persistence.repositories import (
    SqlApplicationRepository,
    SqlApprovalRepository,
    SqlAuditRepository,
    SqlCheckpointRepository,
    SqlConversationRepository,
    SqlJobRepository,
    SqlNodeRepository,
    SqlPreferenceRepository,
    SqlProjectRepository,
    SqlSnapshotRepository,
    SqlUsageRepository,
)
from panos_core.persistence.requirement_repository import (
    SqlRequirementRepository,
)


class SqlUnitOfWork:
    """Async context manager. Rolls back unless :meth:`commit` was called."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        s = self._session
        self.nodes = SqlNodeRepository(s)
        self.projects = SqlProjectRepository(s)
        self.jobs = SqlJobRepository(s)
        self.approvals = SqlApprovalRepository(s)
        self.audit = SqlAuditRepository(s)
        self.checkpoints = SqlCheckpointRepository(s)
        self.snapshots = SqlSnapshotRepository(s)
        self.applications = SqlApplicationRepository(s)
        self.preferences = SqlPreferenceRepository(s)
        self.conversations = SqlConversationRepository(s)
        self.ai_usage = SqlUsageRepository(s)
        self.requirements = SqlRequirementRepository(s)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._session is not None  # noqa: S101
        try:
            await self._session.rollback()
        finally:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("unit of work is not active")
        return self._session

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()


class UnitOfWorkFactory:
    def __init__(self, engine: AsyncEngine) -> None:
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)

    def __call__(self) -> SqlUnitOfWork:
        return SqlUnitOfWork(self._session_factory)
