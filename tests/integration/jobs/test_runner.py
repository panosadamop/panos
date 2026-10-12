"""Job runner: recovery, pause/resume/cancel, limits, notifications, Docker approval."""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

from panos_core.config import PanosSettings
from panos_core.domain.enums import JobStatus, StepStatus
from panos_core.domain.models import JobBudget
from panos_core.jobs.context import JobContext
from panos_core.jobs.runner import INTERRUPTED, JobError, JobRunner
from panos_core.platform.process import spawn_group
from panos_core.shell import process as process_module
from panos_node_api.container import AIOverride, NodeContainer, build_container
from tests.ai_fakes import MockAI
from tests.fakes import FakeDesktop
from tests.integration.jobs.conftest import calc_project, wait_for

pytestmark = pytest.mark.req("BR-023", "FR-022", "US-052", "US-050")

TERMINAL_OR_WAITING = (
    JobStatus.COMPLETED,
    JobStatus.BLOCKED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
    JobStatus.WAITING_FOR_APPROVAL,
    JobStatus.PAUSED,
)


class GatedWorkflow:
    """Test workflow: N phases, each waiting for a gate, with a safe point in between."""

    kind = "gated"

    def __init__(self, phases: int = 3) -> None:
        self.phases = phases
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()
        self.runs = 0

    async def run(self, ctx: JobContext) -> dict[str, Any]:
        self.runs += 1
        done = ctx.state.setdefault("done", 0)
        for i in range(done, self.phases):
            await ctx.safe_point()
            async with ctx.step("test", f"phase {i}"):
                self.entered.set()
                await self.gate.wait()
            ctx.state["done"] = i + 1
            await ctx.save()
        return {"success": True, "phases": ctx.state["done"]}


def alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def gated_runner(node: NodeContainer, workflow: GatedWorkflow) -> JobRunner:
    runner = JobRunner(
        node.runner.services, [workflow], node.events, project_lookup=node.projects.get
    )
    runner.add_listener(node.notifications.on_job_status)
    return runner


@pytest.mark.req("AC-US052-01", "AC-US052-03")
async def test_pause_at_safe_point_and_resume_from_persisted_state(node: NodeContainer) -> None:
    workflow = GatedWorkflow()
    runner = gated_runner(node, workflow)
    job = await runner.submit("gated work", kind="gated")
    await asyncio.wait_for(workflow.entered.wait(), 5)
    await runner.pause(job.id)  # requested while phase 0 runs
    assert (await node.jobs.get(job.id)).status == JobStatus.RUNNING  # not yet: no safe point
    workflow.gate.set()
    job = await wait_for(node, job.id, TERMINAL_OR_WAITING, runner=runner)
    assert job.status == JobStatus.PAUSED and job.state["done"] == 1
    await runner.resume(job.id)
    job = await wait_for(node, job.id, (JobStatus.COMPLETED,), runner=runner)
    assert job.result == {"success": True, "phases": 3}
    assert workflow.runs == 2
    steps = await node.jobs.steps(job.id)
    assert [s.input_summary for s in steps] == ["phase 0", "phase 1", "phase 2"]
    with pytest.raises(JobError):
        await runner.resume(job.id)  # completed jobs cannot be resumed
    await runner.close()


@pytest.mark.req("AC-US050-01", "NFR-REL-002")
async def test_crash_recovery_pauses_interrupted_jobs(
    settings: PanosSettings, mock_ai: MockAI, node: NodeContainer
) -> None:
    workflow = GatedWorkflow()
    runner = gated_runner(node, workflow)
    job = await runner.submit("survive a crash", kind="gated")
    await asyncio.wait_for(workflow.entered.wait(), 5)
    await runner.close()  # simulated crash: task gone, job row still RUNNING
    assert (await node.jobs.get(job.id)).status == JobStatus.RUNNING
    queued = await node.jobs.create(
        "queued before crash", kind="command", input={"argv": [sys.executable, "--version"]}
    )

    # A new node starting on the same database recovers both.
    restarted = await build_container(
        settings, desktop=FakeDesktop(), ai=AIOverride(dict(mock_ai.providers), mock_ai.models)
    )
    try:
        recovered = await restarted.jobs.get(job.id)
        assert recovered.status == JobStatus.PAUSED and recovered.status_reason == INTERRUPTED
        # The queued job starts (it then blocks: a command job needs a project scope).
        q = await wait_for(restarted, queued.id, TERMINAL_OR_WAITING)
        assert q.started_at is not None and q.status == JobStatus.BLOCKED
        assert q.result and q.result["success"] is False
    finally:
        await restarted.close()


@pytest.mark.req("AC-US052-02", "NFR-REL-002")
async def test_cancel_kills_the_running_process_tree(node: NodeContainer, tmp_path: Path) -> None:
    project = await calc_project(node, tmp_path, git_repo=False)
    pid_file = Path(project.path) / "child.pid"
    (Path(project.path) / "sleeper.py").write_text(
        "import subprocess, sys, time, pathlib\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "pathlib.Path('child.pid').write_text(str(child.pid))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    job = await node.runner.submit(
        "long command",
        kind="command",
        project=project,
        input={"argv": [sys.executable, "sleeper.py"]},
    )
    job = await wait_for(node, job.id, TERMINAL_OR_WAITING)
    assert job.status == JobStatus.WAITING_FOR_APPROVAL  # running code needs approval
    approval = (await node.approvals.list_pending())[0]
    await node.approvals.decide(approval.id, approve=True, decided_by="user")
    deadline = time.monotonic() + 30
    while not pid_file.exists():
        assert time.monotonic() < deadline, "command did not start"
        await asyncio.sleep(0.1)
    child = int(pid_file.read_text())
    assert alive(child)

    job = await node.runner.cancel(job.id)
    assert job.status == JobStatus.CANCELLED
    assert job.result and job.result["cancelled"] and "Cancelled by user" in job.result["summary"]
    deadline = time.monotonic() + 10
    while alive(child):
        assert time.monotonic() < deadline, "child process survived cancel"
        await asyncio.sleep(0.1)
    steps = await node.jobs.steps(job.id)
    assert any(s.status in (StepStatus.CANCELLED, StepStatus.FAILED) for s in steps)
    with pytest.raises(JobError):
        await node.runner.cancel(job.id)


@pytest.mark.req("FR-023", "AC-US050-02")
async def test_token_and_runtime_limits_block_the_job(node: NodeContainer, tmp_path: Path) -> None:
    project = await calc_project(node, tmp_path, git_repo=False)
    job = await node.runner.submit("multiply", project=project, budget=JobBudget(max_tokens=1))
    job = await wait_for(node, job.id, TERMINAL_OR_WAITING)
    assert job.status == JobStatus.BLOCKED and "AI tokens" in job.status_reason
    assert job.result and job.result["success"] is False

    workflow = GatedWorkflow(phases=2)
    workflow.gate.set()
    runner = gated_runner(node, workflow)
    slow = await runner.submit("slow", kind="gated", budget=JobBudget(max_runtime_s=1))
    await wait_for(node, slow.id, (JobStatus.COMPLETED,), runner=runner)  # fast enough
    job = await node.jobs.create("over time", kind="gated", budget=JobBudget(max_runtime_s=1))
    await node.jobs.save_state(job.id, {"active_s": 5.0})  # already used 5 s of its 1 s
    await runner.resume(job.id)
    job = await wait_for(node, job.id, TERMINAL_OR_WAITING, runner=runner)
    assert job.status == JobStatus.BLOCKED and "runtime" in job.status_reason
    await runner.close()


@pytest.mark.req("FR-025", "US-051", "AC-US051-02", "AC-US050-03")
async def test_only_user_relevant_transitions_notify(node: NodeContainer) -> None:
    workflow = GatedWorkflow(phases=1)
    workflow.gate.set()
    runner = gated_runner(node, workflow)
    events: list[str] = []
    async with node.events.subscribe() as queue:
        job = await runner.submit("notify me", kind="gated")
        await wait_for(node, job.id, (JobStatus.COMPLETED,), runner=runner)
        while not queue.empty():
            events.append(queue.get_nowait().type)
    kinds = [n.kind for n in node.notifications.sent]
    assert kinds == ["completed"]  # QUEUED/RUNNING are routine: no notification
    assert events.count("job.status") >= 3 and "notification" in events
    await runner.close()


@pytest.mark.req("US-041", "AC-US041-02", "AC-US041-03", "BR-018", "FR-014")
async def test_rejected_docker_execution_spawns_nothing(
    node: NodeContainer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[list[str]] = []
    real_spawn = spawn_group

    async def spy(argv: list[str], **kw: Any) -> Any:
        spawned.append(list(argv))
        return await real_spawn(argv, **kw)

    monkeypatch.setattr(process_module, "spawn_group", spy)
    project = await calc_project(node, tmp_path, git_repo=False)
    job = await node.runner.submit(
        "start the stack",
        kind="command",
        project=project,
        input={"argv": ["docker", "compose", "up", "-d"]},
    )
    job = await wait_for(node, job.id, TERMINAL_OR_WAITING)
    assert job.status == JobStatus.WAITING_FOR_APPROVAL
    approval = (await node.approvals.list_pending())[0]
    assert approval.action_type == "docker.exec"
    await node.approvals.decide(approval.id, approve=False, decided_by="user")
    job = await wait_for(node, job.id, TERMINAL_OR_WAITING)
    assert job.status == JobStatus.BLOCKED and "rejected" in job.status_reason
    assert not any("docker" in Path(a[0]).name.lower() for a in spawned)


@pytest.mark.req("US-041", "AC-US041-01")
async def test_docker_inspection_needs_no_approval(node: NodeContainer, tmp_path: Path) -> None:
    from tests.repos import copy_repo

    root = copy_repo("infra_only", tmp_path)
    project = await node.projects.register(root)
    ctx = await node.context_for(project, actor="agent:devops")
    result = await node.tools.invoke("docker.inspect", {}, ctx)
    assert result.status.value == "SUCCEEDED", result.error
    out = result.output or {}
    assert out["dockerfiles"] and out["dockerfiles"][0]["path"] == "Dockerfile"
    assert out["compose"] and out["compose"][0]["services"]
    assert await node.approvals.list_pending() == []
