import pytest

from panos_core.audit import AuditService
from panos_core.domain import EntityNotFoundError, JobBudget, JobStatus
from panos_core.jobs import InvalidTransitionError, JobService
from panos_core.nodes import ensure_local_node
from panos_core.persistence import UnitOfWorkFactory

pytestmark = pytest.mark.req("FR-022")


@pytest.fixture
async def jobs(uow_factory: UnitOfWorkFactory) -> tuple[JobService, AuditService]:
    node = await ensure_local_node(uow_factory)
    audit = AuditService(uow_factory, node.id)
    return JobService(uow_factory, audit, node.id), audit


@pytest.mark.req("AC-US050-01", "FR-028")
async def test_transitions_are_persisted_timestamped_and_audited(
    jobs: tuple[JobService, AuditService],
) -> None:
    service, audit = jobs
    job = await service.create("implement x", budget=JobBudget(max_tokens=1000))
    assert job.status == JobStatus.QUEUED
    running = await service.transition(job.id, JobStatus.RUNNING, actor="system")
    assert running.started_at is not None
    done = await service.transition(job.id, JobStatus.COMPLETED, actor="system", reason="ok")
    assert done.completed_at is not None
    reloaded = await service.get(job.id)
    assert reloaded.status == JobStatus.COMPLETED
    assert reloaded.budget.max_tokens == 1000
    events = await audit.list(job_id=job.id)
    assert [e.event_type for e in events] == ["job.transition", "job.transition", "job.created"]
    assert events[0].metadata == {"from": "RUNNING", "to": "COMPLETED", "reason": "ok"}


@pytest.mark.req("US-052")
async def test_invalid_transitions_are_refused_and_not_persisted(
    jobs: tuple[JobService, AuditService],
) -> None:
    service, _ = jobs
    job = await service.create("x")
    with pytest.raises(InvalidTransitionError):
        await service.transition(job.id, JobStatus.COMPLETED, actor="system")
    await service.transition(job.id, JobStatus.CANCELLED, actor="user")
    with pytest.raises(InvalidTransitionError):
        await service.transition(job.id, JobStatus.RUNNING, actor="user")
    assert (await service.get(job.id)).status == JobStatus.CANCELLED


async def test_missing_job(jobs: tuple[JobService, AuditService]) -> None:
    service, _ = jobs
    with pytest.raises(EntityNotFoundError):
        await service.get("job_" + "0" * 32)
    with pytest.raises(EntityNotFoundError):
        await service.transition("job_" + "0" * 32, JobStatus.RUNNING, actor="x")
