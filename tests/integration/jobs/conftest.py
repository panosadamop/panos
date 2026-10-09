"""A real node container (no HTTP) with scripted models and a fake desktop."""

import asyncio
import subprocess
from collections.abc import AsyncIterator, Iterable
from pathlib import Path

import pytest

from panos_core.config import PanosSettings
from panos_core.domain import Project
from panos_core.domain.enums import JobStatus
from panos_core.domain.models import Job
from panos_core.jobs.runner import JobRunner
from panos_node_api.container import AIOverride, NodeContainer, build_container
from tests.ai_fakes import MockAI, ScriptedAgents
from tests.fakes import FakeDesktop
from tests.repos import copy_repo


@pytest.fixture
def agents() -> ScriptedAgents:
    return ScriptedAgents()


@pytest.fixture
def mock_ai(agents: ScriptedAgents) -> MockAI:
    ai = MockAI()
    ai.respond(agents)
    return ai


@pytest.fixture
async def node(settings: PanosSettings, mock_ai: MockAI) -> AsyncIterator[NodeContainer]:
    container = await build_container(
        settings, desktop=FakeDesktop(), ai=AIOverride(dict(mock_ai.providers), mock_ai.models)
    )
    try:
        yield container
    finally:
        await container.close()


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


async def calc_project(
    node: NodeContainer, tmp_path: Path, *, git_repo: bool = True, name: str = "calc_unittest"
) -> Project:
    root = copy_repo("calc_unittest", tmp_path)
    if name != "calc_unittest":
        root = root.rename(root.with_name(name))
    if git_repo:
        git(root, "init", "-q")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "initial")
    return await node.projects.register(root)


async def wait_for(
    node: NodeContainer,
    job_id: str,
    statuses: Iterable[JobStatus],
    limit_s: float = 60.0,
    runner: JobRunner | None = None,
) -> Job:
    wanted = set(statuses)
    deadline = asyncio.get_running_loop().time() + limit_s
    while True:
        job = await node.jobs.get(job_id)
        if job.status in wanted and not (runner or node.runner).is_running(job_id):
            return job
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(
                f"job {job_id} is {job.status} ({job.status_reason}), expected {sorted(wanted)}"
            )
        await asyncio.sleep(0.05)
