"""Fixtures wiring the real policy → approval → runtime stack against a temp project."""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from panos_core.approvals import ApprovalService
from panos_core.audit import AuditService
from panos_core.domain import Node, PermissionLevel, Project
from panos_core.jobs import JobService
from panos_core.nodes import ensure_local_node
from panos_core.persistence import UnitOfWorkFactory
from panos_core.policy import PolicyEngine
from panos_core.tools import ToolContext, ToolRuntime
from panos_core.tools.builtin import build_default_registry

NODE_KEY = b"k" * 32


@dataclass
class Stack:
    uow: UnitOfWorkFactory
    node: Node
    audit: AuditService
    approvals: ApprovalService
    jobs: JobService
    runtime: ToolRuntime
    project: Project
    root: Path

    def ctx(
        self,
        *,
        job_id: str | None = None,
        policy_overrides: dict[str, PermissionLevel] | None = None,
        allowed_commands: tuple[tuple[str, ...], ...] = (),
    ) -> ToolContext:
        return ToolContext(
            actor="agent:developer",
            node_id=self.node.id,
            project=self.project,
            job_id=job_id,
            policy_overrides=policy_overrides or {},
            allowed_commands=allowed_commands,
        )


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    # Bytes, so content is identical on every OS (write_text would emit CRLF on Windows).
    (root / "README.md").write_bytes(b"# demo\n")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_bytes(b"print('hello')\n")
    if shutil.which("git"):
        git(root, "init", "-q")
        git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "add", ".")
        git(
            root,
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "init",
        )
    return root


@pytest.fixture
async def stack(uow_factory: UnitOfWorkFactory, project_dir: Path) -> Stack:
    node = await ensure_local_node(uow_factory)
    project = Project(name="proj", path=str(project_dir), node_id=node.id)
    async with uow_factory() as uow:
        await uow.projects.add(project)
        await uow.commit()
    audit = AuditService(uow_factory, node.id)
    approvals = ApprovalService(uow_factory, audit, NODE_KEY)
    jobs = JobService(uow_factory, audit, node.id)
    policy = PolicyEngine()
    runtime = ToolRuntime(
        registry=build_default_registry(policy.policy),
        policy=policy,
        approvals=approvals,
        audit=audit,
        jobs=jobs,
        uow_factory=uow_factory,
    )
    return Stack(uow_factory, node, audit, approvals, jobs, runtime, project, project_dir)
