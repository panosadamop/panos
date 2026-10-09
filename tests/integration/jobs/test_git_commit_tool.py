"""git.commit: approval bound to the exact tree; the user's index is respected (M5-07)."""

from pathlib import Path
from typing import Any

import pytest

from panos_core.domain.enums import ApprovalStatus
from panos_core.tools.runtime import ToolStatus
from panos_node_api.container import NodeContainer
from tests.integration.jobs.conftest import calc_project, git

pytestmark = pytest.mark.req("FR-013", "BR-017", "US-040", "AC-US040-02")


async def approve_and_commit(
    node: NodeContainer, project: object, payload: dict[str, Any]
) -> tuple[object, object]:
    ctx = await node.context_for(project, actor="agent:developer")  # type: ignore[arg-type]
    first = await node.tools.invoke("git.commit", payload, ctx)
    assert first.status == ToolStatus.APPROVAL_REQUIRED and first.approval_id
    decision = await node.approvals.decide(first.approval_id, approve=True, decided_by="user")
    return first, await node.tools.invoke("git.commit", payload, ctx, approval_token=decision.token)


async def test_commit_requires_approval_and_keeps_other_staged_changes(
    node: NodeContainer, tmp_path: Path
) -> None:
    project = await calc_project(node, tmp_path)
    root = Path(project.path)
    (root / "calc.py").write_text("def add(a, b):\n    return a + b  # v2\n", encoding="utf-8")
    (root / "notes.txt").write_text("new file\n", encoding="utf-8")
    (root / "README.md").write_text("# calc (user's own staged edit)\n", encoding="utf-8")
    git(root, "add", "README.md")  # the user staged something unrelated
    _, result = await approve_and_commit(
        node, project, {"message": "feat: v2", "paths": ["calc.py", "notes.txt"]}
    )
    assert result.status == ToolStatus.SUCCEEDED, result.error  # type: ignore[attr-defined]
    out = result.output  # type: ignore[attr-defined]
    assert out["tree_matches_approval"] and out["files"] == 2
    assert set(git(root, "show", "--name-only", "--format=", "HEAD").split()) == {
        "calc.py",
        "notes.txt",
    }
    # The user's unrelated staged change is still staged and not committed.
    assert git(root, "diff", "--cached", "--name-only").split() == ["README.md"]


async def test_nothing_to_commit_and_bad_paths_are_rejected_at_plan(
    node: NodeContainer, tmp_path: Path
) -> None:
    project = await calc_project(node, tmp_path)
    ctx = await node.context_for(project, actor="agent:developer")
    same = await node.tools.invoke("git.commit", {"message": "noop", "paths": ["calc.py"]}, ctx)
    assert same.status == ToolStatus.DENIED and "nothing to commit" in (same.error or "")
    escape = await node.tools.invoke("git.commit", {"message": "x", "paths": ["../evil"]}, ctx)
    assert escape.status == ToolStatus.DENIED
    plain = await calc_project(node, tmp_path / "other", git_repo=False, name="calc_plain")
    ctx2 = await node.context_for(plain, actor="agent:developer")
    not_repo = await node.tools.invoke("git.commit", {"message": "x", "paths": ["calc.py"]}, ctx2)
    assert not_repo.status == ToolStatus.DENIED and "not a Git repository" in (not_repo.error or "")


async def test_token_cannot_be_replayed(node: NodeContainer, tmp_path: Path) -> None:
    project = await calc_project(node, tmp_path)
    root = Path(project.path)
    (root / "calc.py").write_text("x = 1\n", encoding="utf-8")
    payload = {"message": "feat: x", "paths": ["calc.py"]}
    first, result = await approve_and_commit(node, project, payload)
    assert result.status == ToolStatus.SUCCEEDED  # type: ignore[attr-defined]
    approval = await node.approvals.get(first.approval_id)  # type: ignore[attr-defined]
    assert approval is not None and approval.status == ApprovalStatus.CONSUMED
    assert git(root, "rev-list", "--count", "HEAD").strip() == "2"
