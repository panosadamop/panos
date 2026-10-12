"""Estimate-versus-actual learning on a real node (M10-01..05, ADR-031, BR-014/015)."""

from datetime import date
from pathlib import Path

import pytest

from panos_core.domain import Project
from panos_core.domain.effort import ActualStatus, CalibrationStatus, EffortActual, EffortSource
from panos_core.domain.enums import JobStatus
from panos_core.domain.requirements import RequirementType as T
from panos_core.domain.requirements import TraceRelation
from panos_core.effort.service import EffortError
from panos_core.requirements import NewItem
from panos_node_api.container import NodeContainer
from tests.integration.jobs.conftest import wait_for

pytestmark = pytest.mark.req("BR-015", "BR-014")


async def shop(
    node: NodeContainer, tmp_path: Path, name: str = "shop", stories: int = 6
) -> Project:
    root = tmp_path / name
    root.mkdir()
    project = await node.projects.register(root)
    await node.requirements.create_many(
        project.id,
        [NewItem(T.US, f"Story {i}", attributes={"points": 2}) for i in range(1, stories + 1)],
    )
    return project


async def test_log_baseline_suggest_approve_and_estimate_changes(
    node: NodeContainer, tmp_path: Path
) -> None:
    project = await shop(node, tmp_path)
    effort = node.effort
    before = await node.pipeline.estimate(project, record=True)
    assert before.total_hours == 6 * 2 * 6.0 and not any(
        "calibrated" in a for a in before.assumptions
    )

    with pytest.raises(EffortError) as missing:
        await effort.log(project, "US-099", 3)
    assert missing.value.reason == "not_found"
    with pytest.raises(EffortError):
        await effort.log(project, "not a key", 3)

    # Five finished stories took 1.5× longer; one is still in progress (ignored).
    for i in range(1, 6):
        await effort.log(project, f"US-00{i}", 10, work_date=date(2026, 10, 1))
        await effort.log(project, f"US-00{i}", 8, completes=True, role="qa")
    await effort.log(project, "US-006", 2)
    report = await effort.report(project)
    assert report["logged_hours"] == 5 * 18 + 2 and len(report["finished"]) == 5
    assert report["finished"][0]["ratio"] == 1.5

    [suggestion] = await effort.suggest()  # technology unknown → overall only
    assert suggestion.status == CalibrationStatus.SUGGESTED and suggestion.hours_per_point == 9.0
    assert (await node.pipeline.estimate(project)).total_hours == before.total_hours  # not yet

    approved = await effort.decide_calibration(suggestion.id, approve=True, actor="user")
    assert approved.status == CalibrationStatus.APPROVED
    after = await node.pipeline.estimate(project, record=True)
    assert after.total_hours == 6 * 2 * 9.0
    assert any("calibrated from 5 finished stories (overall)" in a for a in after.assumptions)

    # A newer approved calibration supersedes the old one; rejected ones are kept.
    [second] = await effort.suggest()
    await effort.decide_calibration(second.id, approve=True, actor="user")
    statuses = {c.id: c.status for c in await effort.calibrations()}
    assert statuses[approved.id] == CalibrationStatus.SUPERSEDED
    [third] = await effort.suggest()
    rejected = await effort.decide_calibration(third.id, approve=False, actor="user")
    assert rejected.status == CalibrationStatus.REJECTED
    with pytest.raises(EffortError):
        await effort.decide_calibration(third.id, approve=True, actor="user")

    events = [e.event_type for e in await node.audit.list(limit=200)]
    assert {
        "effort.logged",
        "effort.baseline_recorded",
        "effort.calibration_suggested",
        "effort.calibration_decided",
    } <= set(events)


async def test_pending_imports_count_only_after_approval(
    node: NodeContainer, tmp_path: Path
) -> None:
    project = await shop(node, tmp_path, stories=5)
    await node.pipeline.estimate(project, record=True)
    for i in range(1, 6):
        imported = EffortActual(
            project_id=project.id,
            requirement_key=f"US-00{i}",
            source=EffortSource.TRACKER,
            hours=6,
            work_date=date(2026, 10, 2),
            external_ref=f"jira:SHOP-{i}:1",
            completes=True,
        )
        assert await node.effort.add_imported(imported, actor="user")
    duplicate = imported.model_copy(update={"id": "act_" + "0" * 32})
    assert not await node.effort.add_imported(duplicate, actor="user")
    assert await node.effort.suggest() == []  # all pending: nothing to learn from
    pending = await node.effort.actuals(project, status=ActualStatus.PENDING.value)
    for a in pending[:4]:
        await node.effort.decide(a.id, approve=True, actor="user")
    await node.effort.decide(pending[4].id, approve=False, actor="user")
    assert await node.effort.suggest() == []  # 4 approved < 5 stories
    with pytest.raises(EffortError):
        await node.effort.decide(pending[4].id, approve=True, actor="user")


async def test_finished_jobs_record_machine_metrics(node: NodeContainer, tmp_path: Path) -> None:
    import sys

    project = await shop(node, tmp_path, stories=1)
    job = await node.runner.submit(
        "version", kind="command", project=project, input={"argv": [sys.executable, "--version"]}
    )
    await node.requirements.link(
        project.id, "US-001", f"job:{job.id}", TraceRelation.IMPLEMENTS, actor="t"
    )
    job = await wait_for(node, job.id, {JobStatus.COMPLETED, JobStatus.WAITING_FOR_APPROVAL})
    if job.status == JobStatus.WAITING_FOR_APPROVAL:
        [approval] = await node.approvals.list_pending()
        await node.approvals.decide(approval.id, approve=True, decided_by="user")
        job = await wait_for(node, job.id, {JobStatus.COMPLETED})
    machine = await node.effort.actuals(project, source="job")
    assert [(a.requirement_key, a.hours, a.status) for a in machine] == [
        ("US-001", 0.0, ActualStatus.APPROVED)
    ]
    assert machine[0].machine["runtime_s"] >= 0 and machine[0].external_ref == f"{job.id}:US-001"
    report = await node.effort.report(project)
    assert (
        report["machine"]["jobs"] == 1 and report["logged_hours"] == 0
    )  # not mixed with human hours
