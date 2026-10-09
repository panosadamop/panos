"""Path traversal through every filesystem tool (TEST_STRATEGY security tests)."""

import os

import pytest

from panos_core.tools import ToolStatus
from tests.integration.tools.conftest import Stack

pytestmark = pytest.mark.req("NFR-SEC-001", "NFR-SEC-002")

ESCAPES = ["../outside.txt", "src/../../outside.txt", "..\\outside.txt"]
if os.name == "nt":
    ESCAPES += [
        "C:\\Windows\\win.ini",
        "\\\\?\\C:\\Windows\\win.ini",
        "src\\app.py:stream",
        "CON",
        "src\\nul",
        "C:outside.txt",
    ]
else:
    ESCAPES += ["/etc/passwd"]


@pytest.mark.parametrize("raw", ESCAPES)
@pytest.mark.parametrize(
    ("tool", "args"),
    [("fs.read", {}), ("fs.write", {"content": "x"}), ("fs.delete", {}), ("fs.list", {})],
)
async def test_fs_tools_refuse_paths_outside_the_project(
    stack: Stack, tool: str, args: dict[str, object], raw: str
) -> None:
    (stack.root.parent / "outside.txt").write_text("secret", encoding="utf-8")
    result = await stack.runtime.invoke(tool, {"path": raw, **args}, stack.ctx())
    assert result.status == ToolStatus.DENIED, (tool, raw, result)
    assert (stack.root.parent / "outside.txt").read_text(encoding="utf-8") == "secret"


async def test_move_cannot_target_outside(stack: Stack) -> None:
    result = await stack.runtime.invoke(
        "fs.move", {"source": "README.md", "destination": "../stolen.md"}, stack.ctx()
    )
    assert result.status == ToolStatus.DENIED
    assert not (stack.root.parent / "stolen.md").exists()


async def test_rejected_traversal_is_audited(stack: Stack) -> None:
    await stack.runtime.invoke("fs.read", {"path": "../outside.txt"}, stack.ctx())
    events = await stack.audit.list(event_type="tool.rejected")
    assert events and "escapes" in events[0].metadata["reason"]


async def test_git_internals_cannot_be_written(stack: Stack) -> None:
    for raw in (".git/hooks/pre-commit", ".GIT/config" if os.name == "nt" else ".git/config"):
        result = await stack.runtime.invoke("fs.write", {"path": raw, "content": "x"}, stack.ctx())
        assert result.status == ToolStatus.DENIED, raw
        assert result.action == "fs.write.git_internal"
