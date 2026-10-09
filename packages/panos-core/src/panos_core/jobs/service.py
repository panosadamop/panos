"""Job lifecycle service: validated, persisted and audited state transitions."""

from collections.abc import Sequence
from typing import Any

from panos_core.audit import AuditService
from panos_core.domain.clock import utc_now
from panos_core.domain.enums import JobStatus, StepStatus
from panos_core.domain.errors import EntityNotFoundError
from panos_core.domain.models import Job, JobBudget, JobStep
from panos_core.jobs.state_machine import ensure_transition
from panos_core.persistence.unit_of_work import UnitOfWorkFactory

_STARTED_STATES = frozenset({JobStatus.PLANNING, JobStatus.RUNNING})
_SUMMARY_LIMIT = 2000


def _clip(text: str | None) -> str | None:
    if text is None or len(text) <= _SUMMARY_LIMIT:
        return text
    return text[: _SUMMARY_LIMIT - 3] + "..."


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
        kind: str = "coding",
        input: dict[str, Any] | None = None,
    ) -> Job:
        job = Job(
            node_id=self._node_id,
            project_id=project_id,
            objective=objective,
            budget=budget or JobBudget(),
            kind=kind,
            input=input or {},
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
            metadata={"kind": kind},
        )
        return job

    async def get(self, job_id: str) -> Job:
        async with self._uow_factory() as uow:
            job = await uow.jobs.get(job_id)
        if job is None:
            raise EntityNotFoundError("job", job_id)
        return job

    async def list(
        self, *, statuses: Sequence[JobStatus] | None = None, limit: int = 100
    ) -> Sequence[Job]:
        async with self._uow_factory() as uow:
            return await uow.jobs.list(statuses=statuses, limit=limit)

    async def steps(self, job_id: str) -> Sequence[JobStep]:
        async with self._uow_factory() as uow:
            return await uow.jobs.list_steps(job_id)

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
            job.status_reason = reason
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

    async def save_state(self, job_id: str, state: dict[str, Any]) -> None:
        """Persist workflow progress (committed before the next consequential step)."""
        async with self._uow_factory() as uow:
            job = await uow.jobs.get(job_id)
            if job is None:
                raise EntityNotFoundError("job", job_id)
            job.state = state
            await uow.jobs.update(job)
            await uow.commit()

    async def set_result(self, job_id: str, result: dict[str, Any]) -> None:
        async with self._uow_factory() as uow:
            job = await uow.jobs.get(job_id)
            if job is None:
                raise EntityNotFoundError("job", job_id)
            job.result = result
            await uow.jobs.update(job)
            await uow.commit()

    async def start_step(
        self, job_id: str, *, agent: str, summary: str, tool: str | None = None
    ) -> JobStep:
        """Record a workflow step *before* it runs (NFR-REL-001)."""
        async with self._uow_factory() as uow:
            sequence = len(await uow.jobs.list_steps(job_id)) + 1
            step = JobStep(
                job_id=job_id,
                sequence=sequence,
                agent_type=agent,
                tool_name=tool,
                status=StepStatus.RUNNING,
                input_summary=_clip(summary),
                started_at=utc_now(),
            )
            await uow.jobs.add_step(step)
            await uow.commit()
        return step

    async def finish_step(self, step: JobStep, status: StepStatus, output: str = "") -> JobStep:
        step = step.model_copy(
            update={"status": status, "output_summary": _clip(output), "completed_at": utc_now()}
        )
        async with self._uow_factory() as uow:
            await uow.jobs.update_step(step)
            await uow.commit()
        return step
