from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from panos_core.domain import (
    ActionApproval,
    ApprovalStatus,
    AuditEvent,
    Checkpoint,
    EntityNotFoundError,
    Job,
    JobBudget,
    JobStatus,
    JobStep,
    Node,
    PolicyDecision,
    Project,
    new_id,
)
from panos_core.nodes import current_platform, ensure_local_node
from panos_core.persistence import UnitOfWorkFactory


async def _node(uow_factory: UnitOfWorkFactory) -> Node:
    return await ensure_local_node(uow_factory)


async def test_local_node_created_once_and_reused(uow_factory: UnitOfWorkFactory) -> None:
    first = await ensure_local_node(uow_factory)
    second = await ensure_local_node(uow_factory)
    assert first.id == second.id
    assert first.platform == current_platform()
    assert second.last_seen_at >= first.last_seen_at


@pytest.mark.req("FR-004", "US-011", "AC-US011-02")
async def test_project_round_trip_preserves_policy_fields(uow_factory: UnitOfWorkFactory) -> None:
    node = await _node(uow_factory)
    project = Project(name="demo", path="C:/work/demo", node_id=node.id, preferred_ide="idea")
    async with uow_factory() as uow:
        await uow.projects.add(project)
        await uow.commit()
    async with uow_factory() as uow:
        loaded = await uow.projects.get(project.id)
        by_path = await uow.projects.get_by_path("C:/work/demo")
    assert loaded == project
    assert by_path == project
    assert loaded is not None and loaded.created_at.tzinfo is not None


@pytest.mark.req("AC-US011-03")
async def test_same_path_cannot_be_registered_twice(uow_factory: UnitOfWorkFactory) -> None:
    node = await _node(uow_factory)
    async with uow_factory() as uow:
        await uow.projects.add(Project(name="a", path="/srv/x", node_id=node.id))
        await uow.commit()
    async with uow_factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.projects.add(Project(name="b", path="/srv/x", node_id=node.id))


async def test_uncommitted_work_is_rolled_back(uow_factory: UnitOfWorkFactory) -> None:
    node = await _node(uow_factory)
    project = Project(name="demo", path="/srv/demo", node_id=node.id)
    async with uow_factory() as uow:
        await uow.projects.add(project)
    async with uow_factory() as uow:
        assert await uow.projects.get(project.id) is None


async def test_foreign_keys_are_enforced(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.projects.add(Project(name="x", path="/x", node_id=new_id("node")))


@pytest.mark.req("FR-022", "AC-US050-01")
async def test_job_and_steps_persist_state(uow_factory: UnitOfWorkFactory) -> None:
    node = await _node(uow_factory)
    job = Job(
        node_id=node.id,
        objective="implement feature",
        budget=JobBudget(max_tokens=50_000, max_repair_attempts=2),
    )
    step = JobStep(job_id=job.id, sequence=1, tool_name="fs.read")
    async with uow_factory() as uow:
        await uow.jobs.add(job)
        await uow.jobs.add_step(step)
        await uow.commit()

    job.status = JobStatus.WAITING_FOR_APPROVAL
    step.output_summary = "read 3 files"
    async with uow_factory() as uow:
        await uow.jobs.update(job)
        await uow.jobs.update_step(step)
        await uow.commit()

    # A new unit of work simulates a restart: state comes back from disk.
    async with uow_factory() as uow:
        loaded = await uow.jobs.get(job.id)
        steps = await uow.jobs.list_steps(job.id)
    assert loaded == job
    assert loaded is not None and loaded.budget.max_repair_attempts == 2
    assert [s.output_summary for s in steps] == ["read 3 files"]


@pytest.mark.req("FR-010")
async def test_approvals_filter_by_status(uow_factory: UnitOfWorkFactory) -> None:
    now = datetime.now(UTC)
    approvals = [
        ActionApproval(
            action_type="docker.exec",
            target=f"t{i}",
            payload_hash=f"{i:064x}",
            summary="run",
            requested_at=now + timedelta(seconds=i),
            expires_at=now + timedelta(hours=1),
        )
        for i in range(3)
    ]
    approvals[1].status = ApprovalStatus.REJECTED
    async with uow_factory() as uow:
        for a in approvals:
            await uow.approvals.add(a)
        await uow.commit()
    async with uow_factory() as uow:
        pending = await uow.approvals.list_by_status(ApprovalStatus.PENDING)
    assert [a.id for a in pending] == [approvals[0].id, approvals[2].id]


@pytest.mark.req("FR-028")
async def test_audit_log_appends_and_filters_newest_first(uow_factory: UnitOfWorkFactory) -> None:
    node = await _node(uow_factory)
    job_id = new_id("job")
    async with uow_factory() as uow:
        for i in range(3):
            await uow.audit.append(
                AuditEvent(
                    node_id=node.id,
                    job_id=job_id if i < 2 else None,
                    actor="agent:developer",
                    event_type="policy.decision",
                    action=f"action-{i}",
                    policy_decision=PolicyDecision.ALLOW,
                    metadata={"i": i},
                )
            )
        await uow.commit()
    async with uow_factory() as uow:
        for_job = await uow.audit.list(job_id=job_id)
        all_events = await uow.audit.list(event_type="policy.decision", limit=2)
    assert [e.action for e in for_job] == ["action-1", "action-0"]
    assert len(all_events) == 2
    assert for_job[0].metadata == {"i": 1}


def test_audit_repository_exposes_no_update_or_delete() -> None:
    from panos_core.persistence.repositories import SqlAuditRepository

    assert not hasattr(SqlAuditRepository, "update")
    assert not hasattr(SqlAuditRepository, "delete")


async def test_checkpoints_listed_per_project(uow_factory: UnitOfWorkFactory) -> None:
    node = await _node(uow_factory)
    project = Project(name="p", path="/p", node_id=node.id)
    async with uow_factory() as uow:
        await uow.projects.add(project)
        await uow.checkpoints.add(Checkpoint(project_id=project.id, reference="abc123"))
        await uow.commit()
    async with uow_factory() as uow:
        cps = await uow.checkpoints.list_for_project(project.id)
    assert [c.reference for c in cps] == ["abc123"]


async def test_updating_missing_entity_raises(uow_factory: UnitOfWorkFactory) -> None:
    node = await _node(uow_factory)
    async with uow_factory() as uow:
        with pytest.raises(EntityNotFoundError):
            await uow.projects.update(Project(name="ghost", path="/g", node_id=node.id))
