"""Typed Git tools (FR-013, BR-017)."""

import shutil
from pathlib import Path

import pytest

from panos_core.domain import Project
from panos_core.tools import ToolStatus
from tests.integration.projects.conftest import M3
from tests.repos import copy_repo

pytestmark = [
    pytest.mark.req("FR-013", "BR-017"),
    pytest.mark.skipif(shutil.which("git") is None, reason="git not installed"),
]


@pytest.fixture
async def repo(m3: M3, tmp_path: Path) -> Project:
    path = copy_repo("python_fastapi", tmp_path, git=True)
    (path / "src" / "orders" / "api.py").write_text("changed\n", encoding="utf-8")
    (path / "new.txt").write_text("new\n", encoding="utf-8")
    return await m3.projects.register(path)


async def test_status(m3: M3, repo: Project) -> None:
    result = await m3.runtime.invoke("git.status", {}, await m3.ctx(repo))
    assert result.status == ToolStatus.SUCCEEDED, result.error
    out = result.output
    assert out is not None and out["branch"] == "main" and out["dirty"]
    kinds = {e["path"]: e["kind"] for e in out["entries"]}
    assert kinds == {"src/orders/api.py": "changed", "new.txt": "untracked"}


async def test_diff_with_numstat_and_path_filter(m3: M3, repo: Project) -> None:
    ctx = await m3.ctx(repo)
    result = await m3.runtime.invoke("git.diff", {"path": "src/orders/api.py"}, ctx)
    assert result.output is not None
    assert [f["path"] for f in result.output["files"]] == ["src/orders/api.py"]
    assert "+changed" in result.output["diff"]
    stat = await m3.runtime.invoke("git.diff", {"stat_only": True}, ctx)
    assert stat.output is not None and stat.output["diff"] == ""


async def test_log_branches_show_blame(m3: M3, repo: Project) -> None:
    ctx = await m3.ctx(repo)
    log = await m3.runtime.invoke("git.log", {"limit": 5}, ctx)
    assert log.output is not None and log.output["commits"][0]["subject"] == "initial import"
    branches = await m3.runtime.invoke("git.branches", {}, ctx)
    assert branches.output is not None
    assert [b["name"] for b in branches.output["branches"] if b["current"]] == ["main"]
    show = await m3.runtime.invoke("git.show", {"rev": "HEAD", "path": "README.md"}, ctx)
    assert show.output is not None and show.output["text"].startswith("# Orders")
    blame = await m3.runtime.invoke("git.blame", {"path": "README.md"}, ctx)
    assert blame.output is not None and "Tester" in blame.output["text"]


async def test_create_branch_is_local_p1(m3: M3, repo: Project) -> None:
    ctx = await m3.ctx(repo)
    result = await m3.runtime.invoke("git.create_branch", {"name": "feature/login"}, ctx)
    assert result.status == ToolStatus.SUCCEEDED
    assert result.action == "git.mutate.local"
    branches = await m3.runtime.invoke("git.branches", {}, ctx)
    assert branches.output is not None
    assert "feature/login" in [b["name"] for b in branches.output["branches"]]


@pytest.mark.req("NFR-SEC-002")
@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("git.diff", {"base": "--output=/tmp/x"}),
        ("git.log", {"rev": "--all"}),
        ("git.show", {"rev": "-p"}),
        ("git.create_branch", {"name": "-D"}),
        ("git.create_branch", {"name": "main..evil"}),
        ("git.create_branch", {"name": "x", "start_point": "--orphan"}),
    ],
)
async def test_option_injection_is_rejected(
    m3: M3, repo: Project, tool: str, args: dict[str, str]
) -> None:
    result = await m3.runtime.invoke(tool, args, await m3.ctx(repo))
    assert result.status == ToolStatus.INVALID_INPUT


@pytest.mark.req("NFR-SEC-001")
async def test_paths_outside_project_are_refused(m3: M3, repo: Project) -> None:
    result = await m3.runtime.invoke("git.blame", {"path": "../../etc/passwd"}, await m3.ctx(repo))
    assert result.status == ToolStatus.DENIED


async def test_non_repository_is_refused(m3: M3, tmp_path: Path) -> None:
    project = await m3.projects.register(copy_repo("unknown_stack", tmp_path))
    result = await m3.runtime.invoke("git.status", {}, await m3.ctx(project))
    assert result.status == ToolStatus.DENIED
    assert "not a Git repository" in (result.error or "")
