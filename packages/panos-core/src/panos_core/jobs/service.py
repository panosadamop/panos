"""Job lifecycle service: validated, persisted and audited state transitions."""

from panos_core.audit import AuditService
from panos_core.domain.clock import utc_now
from panos_core.domain.enums import JobStatus
from panos_core.domain.errors import EntityNotFoundError
from panos_core.domain.models import Job, JobBudget
from panos_core.jobs.state_machine import ensure_transition
from panos_core.persistence.unit_of_work import UnitOfWorkFactory

_STARTED_STATES = frozenset({JobStatus.PLANNING, JobStatus.RUNNING})


class JobService:
    def __init__(self, uow_factory: UnitOfWorkFactory, audit: AuditService, node_id: str) -> None:
        self._uow_factory = uow_factory
        self._audit = audit
        self._node_id = node_id

    async def create(
        self,
        objective: str,
        *,
        project_id: str | None = None,
        budget: JobBudget | None = None,
        actor: str = "user",
    ) -> Job:
        job = Job(
            node_id=self._node_id,
            project_id=project_id,
            objective=objective,
            budget=budget or JobBudget(),
        )
        async with self._uow_factory() as uow:
            await uow.jobs.add(job)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="job.created",
            action="job.create",
            project_id=project_id,
            job_id=job.id,
        )
        return job

    async def get(self, job_id: str) -> Job:
        async with self._uow_factory() as uow:
            job = await uow.jobs.get(job_id)
        if job is None:
            raise EntityNotFoundError("job", job_id)
        return job

    async def transition(
        self, job_id: str, target: JobStatus, *, actor: str, reason: str = ""
    ) -> Job:
        """Move a job to ``target``. Raises InvalidTransitionError for illegal moves."""
        async with self._uow_factory() as uow:
            job = await uow.jobs.get(job_id)
            if job is None:
                raise EntityNotFoundError("job", job_id)
            previous = job.status
            ensure_transition(previous, target)
            now = utc_now()
            job.status = target
            if target in _STARTED_STATES and job.started_at is None:
                job.started_at = now
            if target.is_terminal:
                job.completed_at = now
            await uow.jobs.update(job)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="job.transition",
            action=f"{previous}->{target}",
            project_id=job.project_id,
            job_id=job.id,
            metadata={"from": previous, "to": target, "reason": reason},
        )
        return job
