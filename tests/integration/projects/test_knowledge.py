"""Project knowledge indexing and search through the service and tool (FR-019/020)."""

from pathlib import Path

import pytest

from panos_core.domain import PrivacyMode
from panos_core.tools import ToolStatus
from tests.integration.projects.conftest import M3
from tests.repos import copy_repo

pytestmark = pytest.mark.req("FR-019", "FR-020", "NFR-PRV-003")


@pytest.mark.req("NFR-PERF-004")
async def test_incremental_indexing(m3: M3, tmp_path: Path) -> None:
    repo = copy_repo("python_fastapi", tmp_path)
    project = await m3.projects.register(repo)
    first = await m3.knowledge.index_project(project)
    assert first.indexed == first.files_seen > 0
    second = await m3.knowledge.index_project(project)
    assert (second.indexed, second.unchanged) == (0, first.files_seen)
    (repo / "README.md").write_text("# Orders\n\nrefund workflow\n", encoding="utf-8")
    (repo / ".editorconfig").unlink()
    third = await m3.knowledge.index_project(project)
    assert (third.indexed, third.removed) == (1, 1)
    hits = await m3.knowledge.search("refund", project_id=project.id)
    assert [h.uri for h in hits] == ["README.md"]


async def test_search_tool_reports_privacy_label(m3: M3, tmp_path: Path) -> None:
    project = await m3.projects.register(copy_repo("python_fastapi", tmp_path))
    await m3.knowledge.index_project(project)
    result = await m3.runtime.invoke(
        "knowledge.search", {"query": "OrderService"}, await m3.ctx(project)
    )
    assert result.status == ToolStatus.SUCCEEDED
    assert result.output is not None
    hit = result.output["hits"][0]
    assert hit["uri"] == "src/orders/api.py"
    assert hit["privacy_mode"] == PrivacyMode.LOCAL_ONLY


async def test_disabled_knowledge_is_not_indexed(m3: M3, tmp_path: Path) -> None:
    project = await m3.projects.register(copy_repo("laravel", tmp_path))
    project.knowledge_enabled = False
    stats = await m3.knowledge.index_project(project)
    assert stats.files_seen == 0
