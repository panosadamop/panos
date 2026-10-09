"""Filesystem tools through the runtime (M2-06)."""

import hashlib
import shutil

import pytest

from panos_core.tools import ToolStatus
from tests.integration.tools.conftest import Stack

pytestmark = pytest.mark.req("FR-004", "NFR-SEC-001")

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


async def test_list_recursive_skips_heavy_dirs(stack: Stack) -> None:
    (stack.root / "node_modules" / "pkg").mkdir(parents=True)
    (stack.root / "node_modules" / "pkg" / "x.js").write_text("x", encoding="utf-8")
    result = await stack.runtime.invoke("fs.list", {"path": ".", "recursive": True}, stack.ctx())
    assert result.status == ToolStatus.SUCCEEDED and result.output
    paths = {e["path"] for e in result.output["entries"]}
    assert "src/app.py" in paths
    assert "node_modules" in paths and "node_modules/pkg/x.js" not in paths
    assert not any(p.startswith(".git/") for p in paths)


async def test_read_reports_hash_and_truncation(stack: Stack) -> None:
    result = await stack.runtime.invoke(
        "fs.read", {"path": "src/app.py", "max_bytes": 5}, stack.ctx()
    )
    assert result.output is not None
    assert result.output["content"] == "print"
    assert result.output["truncated"] is True
    assert result.output["sha256"] == hashlib.sha256(b"print('hello')\n").hexdigest()


async def test_search_finds_matches(stack: Stack) -> None:
    result = await stack.runtime.invoke("fs.search", {"query": "HELLO"}, stack.ctx())
    assert result.output is not None
    assert [(m["path"], m["line"]) for m in result.output["matches"]] == [("src/app.py", 1)]
    bad = await stack.runtime.invoke("fs.search", {"query": "(", "regex": True}, stack.ctx())
    assert bad.status == ToolStatus.DENIED


async def test_write_create_update_with_precondition(stack: Stack) -> None:
    created = await stack.runtime.invoke(
        "fs.write", {"path": "docs/new.md", "content": "v1"}, stack.ctx()
    )
    assert created.output is not None and created.output["created"] is True
    v1 = created.output["sha256"]
    ok = await stack.runtime.invoke(
        "fs.write", {"path": "docs/new.md", "content": "v2", "expected_sha256": v1}, stack.ctx()
    )
    assert ok.status == ToolStatus.SUCCEEDED
    stale = await stack.runtime.invoke(
        "fs.write", {"path": "docs/new.md", "content": "v3", "expected_sha256": v1}, stack.ctx()
    )
    assert stale.status == ToolStatus.FAILED and "changed" in (stale.error or "")
    assert (stack.root / "docs" / "new.md").read_text(encoding="utf-8") == "v2"
    exists = await stack.runtime.invoke(
        "fs.write", {"path": "docs/new.md", "content": "x", "create_only": True}, stack.ctx()
    )
    assert exists.status == ToolStatus.FAILED


async def test_write_leaves_no_temp_files(stack: Stack) -> None:
    await stack.runtime.invoke("fs.write", {"path": "a.txt", "content": "x"}, stack.ctx())
    assert not list(stack.root.glob(".panos-*.tmp"))


async def test_move_inside_project(stack: Stack) -> None:
    result = await stack.runtime.invoke(
        "fs.move", {"source": "src/app.py", "destination": "src/main.py"}, stack.ctx()
    )
    assert result.status == ToolStatus.SUCCEEDED
    assert (stack.root / "src" / "main.py").exists()


async def test_move_overwrite_requires_approval(stack: Stack) -> None:
    result = await stack.runtime.invoke(
        "fs.move",
        {"source": "src/app.py", "destination": "README.md", "overwrite": True},
        stack.ctx(),
    )
    assert result.status == ToolStatus.APPROVAL_REQUIRED
    assert result.action == "fs.delete.unrecoverable"


@needs_git
async def test_delete_tracked_clean_file_is_p2(stack: Stack) -> None:
    result = await stack.runtime.invoke("fs.delete", {"path": "README.md"}, stack.ctx())
    assert result.status == ToolStatus.APPROVAL_REQUIRED
    assert result.action == "fs.delete"


async def test_delete_untracked_or_directory_is_p3(stack: Stack) -> None:
    (stack.root / "scratch.txt").write_text("x", encoding="utf-8")
    untracked = await stack.runtime.invoke("fs.delete", {"path": "scratch.txt"}, stack.ctx())
    assert untracked.action == "fs.delete.unrecoverable"
    directory = await stack.runtime.invoke(
        "fs.delete", {"path": "src", "recursive": True}, stack.ctx()
    )
    assert directory.action == "fs.delete.unrecoverable"
    assert directory.status == ToolStatus.APPROVAL_REQUIRED


@needs_git
async def test_delete_modified_tracked_file_is_p3(stack: Stack) -> None:
    (stack.root / "README.md").write_text("changed", encoding="utf-8")
    result = await stack.runtime.invoke("fs.delete", {"path": "README.md"}, stack.ctx())
    assert result.action == "fs.delete.unrecoverable"


async def test_project_root_cannot_be_deleted_or_moved(stack: Stack) -> None:
    deleted = await stack.runtime.invoke("fs.delete", {"path": ".", "recursive": True}, stack.ctx())
    assert deleted.status == ToolStatus.DENIED
    moved = await stack.runtime.invoke("fs.move", {"source": ".", "destination": "x"}, stack.ctx())
    assert moved.status == ToolStatus.DENIED


async def test_tools_require_an_active_project(stack: Stack) -> None:
    from panos_core.tools import ToolContext

    ctx = ToolContext(actor="agent", node_id=stack.node.id, project=None)
    result = await stack.runtime.invoke("fs.read", {"path": "README.md"}, ctx)
    assert result.status == ToolStatus.DENIED
    assert result.error == "no active project"
