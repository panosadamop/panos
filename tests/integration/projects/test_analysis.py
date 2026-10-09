"""Repository analysis end-to-end through the service (FR-005, US-012, AC-US012-01..03)."""

import shutil
from pathlib import Path

import pytest

from panos_core.tools import ToolStatus
from tests.integration.projects.conftest import M3
from tests.repos import copy_repo

pytestmark = pytest.mark.req("FR-005", "US-012", "BR-011")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@needs_git
@pytest.mark.req("AC-US012-01", "AC-US012-02")
async def test_analysis_snapshot_is_persisted_with_git_state(m3: M3, tmp_path: Path) -> None:
    repo = copy_repo("python_fastapi", tmp_path, git=True)
    (repo / "scratch.txt").write_text("wip", encoding="utf-8")
    project = await m3.projects.register(repo)
    snapshot = await m3.analysis.analyze(project)
    assert snapshot.stack["primary_language"] == "Python"
    assert snapshot.commands["test"] == [["uv", "run", "pytest"]]
    git = snapshot.structure["git"]
    assert git["is_repository"] and git["branch"] == "main"
    assert git["untracked_files"] == 1
    assert snapshot.commit_hash == git["head"]
    assert git["recent_commits"][0]["subject"] == "initial import"
    assert snapshot.confidence >= 0.9 and not snapshot.needs_clarification
    latest = await m3.analysis.latest(project.id)
    assert latest is not None and latest.id == snapshot.id
    audit = await m3.audit.list(event_type="project.analyzed")
    assert audit[0].metadata["snapshot_id"] == snapshot.id


@pytest.mark.req("AC-US012-03")
async def test_unknown_structure_asks_for_clarification(m3: M3, tmp_path: Path) -> None:
    project = await m3.projects.register(copy_repo("unknown_stack", tmp_path))
    snapshot = await m3.analysis.analyze(project)
    assert snapshot.needs_clarification
    assert snapshot.confidence < 0.6
    assert any("build manifest" in q for q in snapshot.clarifications)
    assert any("test command" in q for q in snapshot.clarifications)
    assert snapshot.structure["git"] == {"is_repository": False}


async def test_conflicting_build_systems_are_questioned(m3: M3, tmp_path: Path) -> None:
    project = await m3.projects.register(copy_repo("go_rust_dotnet", tmp_path))
    snapshot = await m3.analysis.analyze(project)
    assert any("Several build systems" in q for q in snapshot.clarifications)


async def test_workspace_declared_monorepo_is_not_questioned(m3: M3, tmp_path: Path) -> None:
    project = await m3.projects.register(copy_repo("gradle_kotlin", tmp_path))
    snapshot = await m3.analysis.analyze(project)
    assert not any("monorepo" in q for q in snapshot.clarifications)
    assert "gradle multi-project" in snapshot.stack["workspaces"]


@pytest.mark.req("FR-012", "NFR-SEC-002")
async def test_discovered_commands_run_without_approval_others_still_ask(
    m3: M3, tmp_path: Path
) -> None:
    """After analysis, `uv run pytest` is P1 (allowlisted); `python script.py` stays P3."""
    project = await m3.projects.register(copy_repo("python_fastapi", tmp_path))
    ctx_before = await m3.ctx(project)
    assert ctx_before.allowed_commands == ()
    await m3.analysis.analyze(project)
    ctx = await m3.ctx(project)
    assert ("uv", "run", "pytest") in ctx.allowed_commands
    from panos_core.tools.builtin.shell import ShellInput, ShellTool

    if shutil.which("uv"):
        plan = await ShellTool().plan(ShellInput(argv=["uv", "run", "pytest"]), ctx)
        assert plan.action == "build.test"
    result = await m3.runtime.invoke("shell.run", {"argv": ["python", "evil.py"]}, ctx)
    assert result.status in (ToolStatus.APPROVAL_REQUIRED, ToolStatus.DENIED)


async def test_remote_urls_are_redacted(m3: M3, tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    repo = copy_repo("django_pip", tmp_path, git=True)
    import subprocess

    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "origin",
            "https://user:ghp_" + "x" * 36 + "@example.invalid/r.git",
        ],
        check=True,
    )
    snapshot = await m3.analysis.analyze(await m3.projects.register(repo))
    remotes = snapshot.structure["git"]["remotes"]
    assert remotes and "ghp_" not in remotes[0]["url"]
