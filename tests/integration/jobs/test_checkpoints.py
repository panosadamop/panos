"""Shadow-Git checkpoints and rollback (ADR-012, US-023)."""

import subprocess
from pathlib import Path

import pytest

from panos_core.audit import AuditService
from panos_core.checkpoints import CheckpointError, CheckpointService
from panos_core.config import PanosSettings
from panos_core.domain import Project
from panos_core.nodes import ensure_local_node
from panos_core.persistence import UnitOfWorkFactory
from panos_core.projects import ProjectService

pytestmark = pytest.mark.req("FR-024", "US-023")


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
async def stack(
    uow_factory: UnitOfWorkFactory, settings: PanosSettings
) -> tuple[CheckpointService, ProjectService]:
    node = await ensure_local_node(uow_factory)
    audit = AuditService(uow_factory, node.id)
    return (
        CheckpointService(uow_factory, audit, settings),
        ProjectService(uow_factory, audit, settings, node.id),
    )


def seed(root: Path) -> None:
    write(root / "src" / "app.py", b"print('v1')\r\n")  # CRLF must survive byte-exact
    write(root / "README.md", b"# demo\n")
    write(root / "data.bin", bytes(range(256)))
    write(root / ".gitignore", b"build/\n")
    write(root / "build" / "out.txt", b"generated")


def mutate(root: Path) -> None:
    write(root / "src" / "app.py", b"print('v2')\n")
    (root / "README.md").unlink()
    write(root / "src" / "new" / "feature.py", b"x = 1\n")
    write(root / "build" / "out.txt", b"regenerated")  # ignored: not checkpointed


async def check_rollback(stack: tuple[CheckpointService, ProjectService], root: Path) -> Project:
    checkpoints, projects = stack
    project = await projects.register(root)
    checkpoint = await checkpoints.create(project, job_id=None, actor="test", label="before")
    mutate(root)
    changes = {(c.status, c.path) for c in await checkpoints.changes(project, checkpoint)}
    assert changes == {("M", "src/app.py"), ("D", "README.md"), ("A", "src/new/feature.py")}
    assert "print('v2')" in await checkpoints.diff(project, checkpoint)

    report = await checkpoints.rollback(project, checkpoint, actor="user")
    assert report.verified
    assert set(report.restored) == {"src/app.py", "README.md"}
    assert report.deleted == ["src/new/feature.py"]
    assert (root / "src" / "app.py").read_bytes() == b"print('v1')\r\n"
    assert (root / "README.md").read_bytes() == b"# demo\n"
    assert (root / "data.bin").read_bytes() == bytes(range(256))
    assert not (root / "src" / "new").exists()
    assert (root / "build" / "out.txt").read_bytes() == b"regenerated"  # ignored, untouched
    return project


@pytest.mark.req("AC-US023-01", "AC-US023-03")
async def test_rollback_restores_exact_bytes_in_non_git_project(
    stack: tuple[CheckpointService, ProjectService], tmp_path: Path
) -> None:
    root = tmp_path / "plain"
    seed(root)
    await check_rollback(stack, root)
    assert not (root / ".git").exists()


@pytest.mark.req("AC-US023-02")
async def test_rollback_never_touches_user_git_history_index_or_stash(
    stack: tuple[CheckpointService, ProjectService], tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    seed(root)
    git(root, "init", "-q")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    write(root / "staged.txt", b"staged")
    git(root, "add", "staged.txt")
    before = (
        git(root, "rev-parse", "HEAD"),
        git(root, "diff", "--cached", "--name-only"),
        git(root, "stash", "list"),
        (root / ".git" / "index").read_bytes(),
    )
    project = await check_rollback(stack, root)
    after = (
        git(root, "rev-parse", "HEAD"),
        git(root, "diff", "--cached", "--name-only"),
        git(root, "stash", "list"),
        (root / ".git" / "index").read_bytes(),
    )
    assert before == after
    checkpoints, _ = stack
    assert checkpoints.shadow_dir(project.id).is_dir()
    assert not checkpoints.shadow_dir(project.id).is_relative_to(root)


async def test_rollback_is_itself_reversible_and_checks_ownership(
    stack: tuple[CheckpointService, ProjectService], tmp_path: Path
) -> None:
    checkpoints, projects = stack
    a, b = tmp_path / "a", tmp_path / "b"
    seed(a)
    seed(b)
    pa, pb = await projects.register(a), await projects.register(b)
    cp = await checkpoints.create(pa, job_id=None, actor="test")
    with pytest.raises(CheckpointError):
        await checkpoints.rollback(pb, cp, actor="user")
    with pytest.raises(CheckpointError):
        await checkpoints.get("chk_missing")
    write(a / "late.txt", b"keep me?")
    report = await checkpoints.rollback(pa, cp, actor="user")
    assert not (a / "late.txt").exists()
    # The pre-rollback state is itself a shadow commit: rolling forward is possible.
    assert len(report.safety_reference) == 40
