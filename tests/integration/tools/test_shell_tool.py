"""Controlled shell tool with real processes (M2-07)."""

import asyncio
import shutil
import sys
import time
from unittest.mock import AsyncMock

import psutil
import pytest

from panos_core.tools import ToolStatus
from tests.integration.tools.conftest import Stack

pytestmark = pytest.mark.req("FR-012")

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
PY = sys.executable
# Lets the test run a python helper as an allowlisted (P1) command.
ALLOW_PY = ((PY, "-c"),)


@needs_git
@pytest.mark.req("FR-013")
async def test_git_status_runs_and_captures_output(stack: Stack) -> None:
    result = await stack.runtime.invoke(
        "shell.run", {"argv": ["git", "status", "--short"]}, stack.ctx()
    )
    assert result.status == ToolStatus.SUCCEEDED, result.error
    assert result.action == "git.read"
    assert result.output is not None
    assert result.output["exit_code"] == 0
    assert result.output["duration_s"] >= 0


async def test_exit_code_and_stderr_are_reported(stack: Stack) -> None:
    code = "import sys; sys.stderr.write('boom'); sys.exit(3)"
    result = await stack.runtime.invoke(
        "shell.run", {"argv": [PY, "-c", code]}, stack.ctx(allowed_commands=ALLOW_PY)
    )
    assert result.status == ToolStatus.SUCCEEDED  # the tool worked; the command failed
    assert result.output is not None
    assert result.output["exit_code"] == 3
    assert result.output["stderr"] == "boom"


async def test_output_is_capped(stack: Stack) -> None:
    code = "print('x' * 50000)"
    result = await stack.runtime.invoke(
        "shell.run",
        {"argv": [PY, "-c", code], "max_output_bytes": 2048},
        stack.ctx(allowed_commands=ALLOW_PY),
    )
    assert result.output is not None
    assert len(result.output["stdout"]) == 2048
    assert result.output["stdout_truncated"] is True


async def test_secrets_are_not_passed_to_children(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("SAFE_VALUE", "visible")
    code = "import os; print(os.environ.get('OPENAI_API_KEY'), os.environ.get('SAFE_VALUE'))"
    result = await stack.runtime.invoke(
        "shell.run", {"argv": [PY, "-c", code]}, stack.ctx(allowed_commands=ALLOW_PY)
    )
    assert result.output is not None
    assert result.output["stdout"].split() == ["None", "visible"]


async def test_cwd_must_stay_in_project(stack: Stack) -> None:
    result = await stack.runtime.invoke(
        "shell.run", {"argv": ["git", "status"], "cwd": ".."}, stack.ctx()
    )
    assert result.status == ToolStatus.DENIED
    assert "escapes" in (result.error or "")


async def test_timeout_kills_the_process(stack: Stack) -> None:
    code = "import time; time.sleep(60)"
    started = time.monotonic()
    result = await stack.runtime.invoke(
        "shell.run",
        {"argv": [PY, "-c", code], "timeout_s": 1},
        stack.ctx(allowed_commands=ALLOW_PY),
    )
    assert result.status == ToolStatus.TIMED_OUT
    assert time.monotonic() - started < 30


@pytest.mark.req("US-052", "AC-US052-02")
async def test_cancel_kills_the_whole_process_tree(stack: Stack, tmp_path: object) -> None:
    pid_file = stack.root / "child.pid"
    code = (
        "import subprocess, sys, time; "
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
        f"open(r'{pid_file}', 'w').write(str(c.pid)); time.sleep(120)"
    )
    ctx = stack.ctx(allowed_commands=ALLOW_PY)
    task = asyncio.create_task(stack.runtime.invoke("shell.run", {"argv": [PY, "-c", code]}, ctx))
    for _ in range(200):
        if pid_file.exists() and pid_file.read_text():
            break
        await asyncio.sleep(0.05)
    child_pid = int(pid_file.read_text())
    assert psutil.pid_exists(child_pid)
    ctx.cancel.cancel()
    result = await asyncio.wait_for(task, timeout=30)
    assert result.status == ToolStatus.CANCELLED
    for _ in range(100):
        if not psutil.pid_exists(child_pid):
            break
        await asyncio.sleep(0.05)
    assert not psutil.pid_exists(child_pid), "grandchild survived cancellation"


@pytest.mark.req("AC-US041-02", "AC-US041-03")
async def test_docker_execution_waits_for_approval_and_rejection_runs_nothing(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No docker binary is needed: classification happens before any process starts."""
    fake = stack.root / ("docker.exe" if sys.platform == "win32" else "docker")
    fake.write_text("", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(stack.root))
    spawn = AsyncMock()
    monkeypatch.setattr("panos_core.tools.builtin.shell.run_process", spawn)

    job = await stack.jobs.create("run container", project_id=stack.project.id)
    from panos_core.domain import JobStatus

    await stack.jobs.transition(job.id, JobStatus.RUNNING, actor="system")
    result = await stack.runtime.invoke(
        "shell.run", {"argv": ["docker", "run", "alpine"]}, stack.ctx(job_id=job.id)
    )
    assert result.status == ToolStatus.APPROVAL_REQUIRED
    assert result.action == "docker.exec"
    assert (await stack.jobs.get(job.id)).status == JobStatus.WAITING_FOR_APPROVAL

    assert result.approval_id is not None
    decision = await stack.approvals.decide(result.approval_id, approve=False, decided_by="user")
    assert decision.token is None
    retry = await stack.runtime.invoke(
        "shell.run", {"argv": ["docker", "run", "alpine"]}, stack.ctx(job_id=job.id)
    )
    assert retry.status == ToolStatus.APPROVAL_REQUIRED  # a new request, never execution
    spawn.assert_not_called()


@pytest.mark.req("AC-US041-01")
async def test_docker_inspection_needs_no_approval(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = stack.root / ("docker.exe" if sys.platform == "win32" else "docker")
    fake.write_text("", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(stack.root))
    from panos_core.tools.builtin.shell import ShellInput, ShellTool

    plan = await ShellTool().plan(ShellInput(argv=["docker", "compose", "config"]), stack.ctx())
    assert plan.action == "docker.inspect"
