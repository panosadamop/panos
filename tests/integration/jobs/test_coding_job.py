"""Coding workflow end to end on a fixture repo with scripted agents (M5-05/06/07)."""

import json
from pathlib import Path

import pytest

from panos_core.domain.enums import ApprovalStatus, JobStatus, PrivacyMode, StepStatus
from panos_core.domain.models import JobBudget
from panos_node_api.container import NodeContainer
from tests.ai_fakes import (
    APPROVE,
    DIAGNOSIS,
    FIX_MULTIPLY,
    MULTIPLY_PLAN,
    MockAI,
    ScriptedAgents,
    multiply_change,
)
from tests.integration.jobs.conftest import calc_project, git, wait_for

pytestmark = pytest.mark.req("FR-006", "US-020", "BR-011")

OBJECTIVE = "Add a multiply(a, b) function to calc with a unit test (FR-006)"
DONE = (
    JobStatus.COMPLETED,
    JobStatus.BLOCKED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
    JobStatus.WAITING_FOR_APPROVAL,
)


@pytest.mark.req(
    "AC-US020-01",
    "AC-US020-02",
    "AC-US020-03",
    "AC-US020-04",
    "NFR-REL-001",
    "AC-US040-01",
    "AC-US040-02",
    "US-040",
    "FR-013",
    "BR-017",
    "AC-US050-03",
    "FR-025",
)
async def test_feature_is_planned_checkpointed_implemented_tested_reviewed_and_committed(
    node: NodeContainer, tmp_path: Path, agents: ScriptedAgents, mock_ai: MockAI
) -> None:
    project = await calc_project(node, tmp_path)
    root = Path(project.path)
    job = await node.runner.submit(OBJECTIVE, project=project)
    job = await wait_for(node, job.id, DONE)

    # The commit waits for approval; nothing was committed (no auto-commit).
    assert job.status == JobStatus.WAITING_FOR_APPROVAL, job.status_reason
    assert git(root, "rev-list", "--count", "HEAD").strip() == "1"
    assert agents.roles() == ["planner", "developer", "reviewer"]
    assert mock_ai.cloud.calls == []  # LOCAL_ONLY project: local models only

    report = job.state["report"]
    assert report["plan"] == MULTIPLY_PLAN["summary"]
    assert {(c["status"], c["path"]) for c in report["changed_files"]} == {
        ("M", "calc.py"),
        ("A", "tests/test_multiply.py"),
    }
    assert report["verification"] == [
        {"kind": "test", "argv": ["python", "manage.py", "test"], "ok": True, "exit_code": 0}
    ]
    assert report["review"]["verdict"] == "approve"
    assert report["traceability"][0]["id"] == "FR-006"
    assert "integers only" in report["assumptions"]
    assert (root / "tests" / "test_multiply.py").is_file()

    # Steps were recorded in order, each persisted before it ran (NFR-REL-001).
    steps = await node.jobs.steps(job.id)
    summaries = [s.input_summary or "" for s in steps if s.tool_name is None or s.agent_type]
    assert summaries[0].startswith("Analyze") and any(s.startswith("Plan") for s in summaries)
    assert all(s.started_at is not None for s in steps)

    # The approval shows the change (AC-US040-01) and the user was notified (AC-US050-03).
    pending = await node.approvals.list_pending()
    assert len(pending) == 1 and pending[0].action_type == "git.commit"
    assert "calc.py" in pending[0].summary and "feat(calc): add multiply" in pending[0].summary
    assert [n.kind for n in node.notifications.sent][-1] == "approval"

    decision = await node.approvals.decide(pending[0].id, approve=True, decided_by="user")
    assert decision.token
    job = await wait_for(node, job.id, DONE)
    assert job.status == JobStatus.COMPLETED
    commit = job.result["commit"] if job.result else {}
    assert commit["status"] == "committed" and commit["tree_matches_approval"]
    assert git(root, "log", "-1", "--format=%s").strip() == "feat(calc): add multiply"
    assert git(root, "status", "--porcelain").strip() == ""
    assert (await node.approvals.get(pending[0].id)).status == ApprovalStatus.CONSUMED  # type: ignore[union-attr]
    assert [n.kind for n in node.notifications.sent][-1] == "completed"


@pytest.mark.req("AC-US040-02", "US-040")
async def test_rejected_commit_leaves_changes_uncommitted(
    node: NodeContainer, tmp_path: Path
) -> None:
    project = await calc_project(node, tmp_path)
    job = await node.runner.submit(OBJECTIVE, project=project)
    job = await wait_for(node, job.id, DONE)
    approval = (await node.approvals.list_pending())[0]
    await node.approvals.decide(approval.id, approve=False, decided_by="user")
    job = await wait_for(node, job.id, (JobStatus.COMPLETED, JobStatus.BLOCKED))
    assert job.status == JobStatus.COMPLETED
    assert job.result and job.result["commit"] == {"status": "rejected"}
    assert any("not approved" in u for u in job.result["unresolved"])
    assert git(Path(project.path), "rev-list", "--count", "HEAD").strip() == "1"


@pytest.mark.req("AC-US040-02", "BR-017")
async def test_change_after_approval_invalidates_the_commit(
    node: NodeContainer, tmp_path: Path
) -> None:
    project = await calc_project(node, tmp_path)
    root = Path(project.path)
    job = await node.runner.submit(OBJECTIVE, project=project)
    await wait_for(node, job.id, DONE)
    approval = (await node.approvals.list_pending())[0]
    (root / "calc.py").write_bytes((root / "calc.py").read_bytes() + b"# sneaky\n")
    await node.approvals.decide(approval.id, approve=True, decided_by="user")
    job = await wait_for(node, job.id, (JobStatus.COMPLETED, JobStatus.BLOCKED))
    assert job.result and job.result["commit"]["status"] == "denied"
    assert "action_changed" in job.result["commit"]["error"]
    assert (await node.approvals.get(approval.id)).status == ApprovalStatus.INVALIDATED  # type: ignore[union-attr]
    assert git(root, "rev-list", "--count", "HEAD").strip() == "1"


@pytest.mark.req("US-022", "AC-US022-01", "AC-US022-03", "FR-023")
async def test_failing_tests_are_diagnosed_and_repaired(
    node: NodeContainer, tmp_path: Path, agents: ScriptedAgents
) -> None:
    agents.queues["developer"] = [multiply_change("a + b + 1"), FIX_MULTIPLY]
    project = await calc_project(node, tmp_path, git_repo=False)
    job = await node.runner.submit(OBJECTIVE, project=project, input={"commit": False})
    job = await wait_for(node, job.id, DONE)
    assert job.status == JobStatus.COMPLETED, job.status_reason
    assert job.result and job.result["repair_attempts"] == 1
    assert job.result["verification"][0]["ok"]
    assert job.result["commit"] == {"status": "skipped"}  # not a Git repository
    assert agents.roles() == ["planner", "developer", "qa", "developer", "reviewer"]
    steps = await node.jobs.steps(job.id)
    repair = next(s for s in steps if (s.input_summary or "").startswith("Repair attempt 1"))
    assert repair.status == StepStatus.SUCCEEDED and repair.output_summary == "fixed"
    failed_run = next(s for s in steps if (s.input_summary or "") == "Verify: test")
    assert failed_run.output_summary == "exit 1"


@pytest.mark.req("AC-US022-03")
async def test_a_fix_that_cannot_be_applied_is_a_failed_attempt_not_a_dead_end(
    node: NodeContainer, tmp_path: Path, agents: ScriptedAgents
) -> None:
    bad_fix = {
        "summary": "wrong anchor",
        "edits": [{"path": "calc.py", "action": "edit", "search": "not there", "replace": "x"}],
    }
    agents.queues["developer"] = [multiply_change("a + b + 1"), bad_fix, FIX_MULTIPLY]
    project = await calc_project(node, tmp_path, git_repo=False)
    job = await node.runner.submit(OBJECTIVE, project=project, input={"commit": False})
    job = await wait_for(node, job.id, DONE)
    assert job.status == JobStatus.COMPLETED, job.status_reason
    assert job.result and job.result["repair_attempts"] == 2
    steps = await node.jobs.steps(job.id)
    first = next(s for s in steps if (s.input_summary or "") == "Repair attempt 1")
    assert first.status == StepStatus.FAILED
    assert (first.output_summary or "").startswith("fix not applied")


@pytest.mark.req("AC-US022-02", "NFR-REL-004", "AC-US050-02", "US-022")
async def test_repairs_stop_at_the_limit_and_never_report_success(
    node: NodeContainer, tmp_path: Path, agents: ScriptedAgents
) -> None:
    broken = multiply_change("a + b + 1")
    agents.queues["developer"] = [broken] + [{"summary": "try", "edits": []}] * 5
    project = await calc_project(node, tmp_path, git_repo=False)
    job = await node.runner.submit(
        OBJECTIVE, project=project, budget=JobBudget(max_repair_attempts=2)
    )
    job = await wait_for(node, job.id, DONE)
    assert job.status == JobStatus.BLOCKED
    assert "after 2 repair attempt(s)" in job.status_reason
    assert job.result and job.result["success"] is False
    assert job.result["diagnosis"] == DIAGNOSIS
    assert job.result["failures"][0]["kind"] == "test"
    assert agents.roles().count("qa") == 2
    assert [n.kind for n in node.notifications.sent][-1] == "blocked"
    # Resuming a blocked job continues from its persisted state (no re-planning).
    agents.queues["developer"] = [FIX_MULTIPLY]
    await node.runner.resume(job.id)
    job = await wait_for(node, job.id, (JobStatus.BLOCKED, JobStatus.COMPLETED))
    assert agents.roles().count("planner") == 1


@pytest.mark.req("US-023", "AC-US023-01", "FR-024")
async def test_job_changes_can_be_rolled_back(node: NodeContainer, tmp_path: Path) -> None:
    project = await calc_project(node, tmp_path, git_repo=False)
    root = Path(project.path)
    before = (root / "calc.py").read_bytes()
    job = await node.runner.submit(OBJECTIVE, project=project)
    job = await wait_for(node, job.id, DONE)
    assert (root / "calc.py").read_bytes() != before
    checkpoint = (await node.checkpoints.for_job(job.id))[0]
    report = await node.checkpoints.rollback(project, checkpoint, actor="user")
    assert report.verified and (root / "calc.py").read_bytes() == before
    assert not (root / "tests" / "test_multiply.py").exists()


async def test_invalid_edits_block_the_job(
    node: NodeContainer, tmp_path: Path, agents: ScriptedAgents
) -> None:
    agents.queues["developer"] = [
        {
            "summary": "bad",
            "edits": [
                {"path": "calc.py", "action": "edit", "search": "not in file", "replace": "x"}
            ],
        }
    ]
    project = await calc_project(node, tmp_path, git_repo=False)
    job = await node.runner.submit(OBJECTIVE, project=project)
    job = await wait_for(node, job.id, DONE)
    assert job.status == JobStatus.BLOCKED
    assert "could not apply the proposed edits" in job.status_reason


async def test_unusable_agent_output_blocks_instead_of_failing(
    node: NodeContainer, tmp_path: Path, agents: ScriptedAgents
) -> None:
    agents.queues["developer"] = ["no idea", "still no JSON"]
    project = await calc_project(node, tmp_path, git_repo=False)
    job = await node.runner.submit(OBJECTIVE, project=project)
    job = await wait_for(node, job.id, DONE)
    assert job.status == JobStatus.BLOCKED  # resumable, unlike FAILED
    assert "unusable output" in job.status_reason


async def test_invalid_agent_output_gets_one_retry(
    node: NodeContainer, tmp_path: Path, agents: ScriptedAgents
) -> None:
    agents.queues["planner"] = ["I think we should add multiply.", MULTIPLY_PLAN]
    agents.queues["reviewer"] = ["```json\n" + json.dumps(APPROVE) + "\n```"]
    project = await calc_project(node, tmp_path, git_repo=False)
    job = await node.runner.submit(OBJECTIVE, project=project)
    job = await wait_for(node, job.id, DONE)
    assert job.status == JobStatus.COMPLETED
    assert agents.roles()[:2] == ["planner", "planner"]


@pytest.mark.req("BR-004", "NFR-SEC-005")
async def test_standard_project_jobs_may_use_cloud_local_only_never(
    node: NodeContainer, tmp_path: Path, mock_ai: MockAI
) -> None:
    project = await calc_project(node, tmp_path, git_repo=False, name="calc_std")
    project = await node.projects.set_privacy_mode(project.id, PrivacyMode.STANDARD, actor="user")
    job = await node.runner.submit(OBJECTIVE, project=project)
    await wait_for(node, job.id, DONE)
    assert mock_ai.cloud.calls  # STANDARD coding work prefers the efficient cloud tier
    async with node.uow_factory() as uow:
        tokens, cost = await uow.ai_usage.totals_for_job(job.id)
    assert tokens > 0 and cost > 0
