"""Brief → interview → spec → approved stack → scaffold → docs → skills (M6-04..09)."""

from pathlib import Path

import pytest

from panos_core.config import PanosSettings
from panos_core.domain.enums import JobStatus
from panos_core.domain.requirements import InterviewStatus
from panos_core.domain.requirements import RequirementStatus as S
from panos_core.domain.requirements import RequirementType as T
from panos_core.requirements import RequirementsError
from panos_node_api.container import AIOverride, NodeContainer, build_container
from tests.ai_fakes import MockAI, ScriptedAgents
from tests.fakes import FakeDesktop
from tests.integration.jobs.conftest import wait_for

pytestmark = pytest.mark.req("FR-007", "BR-009")

BRIEF = "We need an online shop where customers buy our handmade products."


async def interviewed(node: NodeContainer, brief: str = BRIEF, name: str = "shop") -> str:
    interview = await node.pipeline.start(brief, name=name)
    await node.pipeline.answer(interview.id, "q1", "Card payments only")
    await node.pipeline.answer(interview.id, "q2", "About 500 orders a day")
    await node.pipeline.answer(interview.id, "q3", None)  # skipped
    return interview.id


@pytest.mark.req("US-030", "AC-US030-01", "AC-US030-02")
async def test_interview_asks_targeted_questions_and_survives_restart(
    node: NodeContainer, settings: PanosSettings, mock_ai: MockAI
) -> None:
    interview = await node.pipeline.start(BRIEF, name="shop")
    project = await node.projects.get("shop")
    assert Path(project.path).is_dir() and Path(project.path).parent == settings.workspace_dir
    assert interview.status == InterviewStatus.OPEN
    assert [q.id for q in interview.questions] == ["q1", "q2", "q3"]
    assert "payment methods are not specified" in interview.ambiguities
    await node.pipeline.answer(interview.id, "q1", "Card payments only")
    assert mock_ai.cloud.calls == []  # new projects are LOCAL_ONLY: local model only

    restarted = await build_container(
        settings, desktop=FakeDesktop(), ai=AIOverride(dict(mock_ai.providers), mock_ai.models)
    )
    try:
        resumed = await restarted.pipeline.get_interview(interview.id)
        assert resumed.questions[0].answer == "Card payments only"
        assert resumed.questions[0].answered_by == "user"
        assert resumed.status == InterviewStatus.OPEN
        await restarted.pipeline.answer(interview.id, "q2", "500/day")
        done = await restarted.pipeline.answer(interview.id, "q3", None)
        assert done.status == InterviewStatus.READY and done.questions[2].skipped
    finally:
        await restarted.close()
    with pytest.raises(RequirementsError):
        await node.pipeline.answer(interview.id, "q9", "x")


@pytest.mark.req("AC-US030-03", "FR-008", "US-021")
async def test_completion_creates_traceable_drafts_with_provenance(
    node: NodeContainer, agents: ScriptedAgents
) -> None:
    interview_id = await interviewed(node)
    with pytest.raises(RequirementsError):
        await node.pipeline.complete("itv_" + "0" * 32)
    items = await node.pipeline.complete(interview_id)
    by_type: dict[T, list[str]] = {}
    for item in items:
        by_type.setdefault(item.type, []).append(item.key)
    assert by_type[T.BR] == ["BR-001"] and by_type[T.FR] == ["FR-001", "FR-002"]
    assert by_type[T.US] == ["US-001", "US-002"]
    assert by_type[T.AC] == ["AC-US001-01", "AC-US001-02", "AC-US002-01"]
    assert by_type[T.TC] == ["TC-001", "TC-002"] and by_type[T.NFR] == ["NFR-001"]
    assert all(i.status == S.DRAFT for i in items)
    project = await node.projects.get("shop")
    fr1 = await node.requirements.get(project.id, "FR-001")
    assert fr1.parent_key == "BR-001"
    assert f"answer:{interview_id}/q1" in fr1.source  # answers are provenance
    # The skipped question became an explicit assumption.
    assumptions = await node.requirements.list_latest(project.id, type=T.ASSUMPTION)
    assert any(a.title.startswith("Unanswered: Do customers need accounts?") for a in assumptions)
    # No stack in the brief: the architect proposed one, as a DRAFT ADR.
    [adr] = await node.requirements.list_latest(project.id, type=T.ADR)
    assert adr.attributes == {
        "pack": "python-fastapi",
        "addons": ["postgresql", "docker"],
        "explicit": False,
    }
    assert "solutionarchitect" in agents.roles()
    links = {(link.source_key, link.target) for link in await node.requirements.links(project.id)}
    assert ("API-001", "FR-001") in links and ("DATA-001", "FR-001") in links
    gaps = (await node.requirements.traceability(project.id))["gaps"]
    assert {"key": "AC-US001-02", "missing": "TC"} in gaps
    with pytest.raises(RequirementsError):
        await node.pipeline.complete(interview_id)  # only once


@pytest.mark.req("US-021", "BR-008")
async def test_a_stack_named_in_the_brief_is_honoured_without_asking(
    node: NodeContainer, agents: ScriptedAgents
) -> None:
    interview_id = await interviewed(
        node,
        "An internal Spring Boot + Vaadin app on PostgreSQL to track contracts.",
        name="contracts",
    )
    interview = await node.pipeline.get_interview(interview_id)
    assert interview.explicit_stack == "java-vaadin"
    await node.pipeline.complete(interview_id)
    project = await node.projects.get("contracts")
    [adr] = await node.requirements.list_latest(project.id, type=T.ADR)
    assert adr.attributes["pack"] == "java-vaadin" and adr.attributes["explicit"] is True
    assert "postgresql" in adr.attributes["addons"]
    assert "solutionarchitect" not in agents.roles()


@pytest.mark.req("FR-007", "BR-013", "FR-030", "US-033", "AC-US033-01", "AC-US033-02", "BR-014")
async def test_scaffold_requires_approved_stack_then_docs_and_versioned_skills(
    node: NodeContainer,
) -> None:
    interview_id = await interviewed(node)
    await node.pipeline.complete(interview_id)
    project = await node.projects.get("shop")
    root = Path(project.path)
    with pytest.raises(RequirementsError) as err:
        await node.pipeline.scaffold(project)
    assert err.value.reason == "not_approved" and "ADR-001" in str(err.value)

    await node.requirements.approve(project.id, "BR-001", actor="owner")
    await node.requirements.approve(project.id, "ADR-001", actor="owner")
    await node.requirements.approve(project.id, "NFR-001", actor="owner")
    report = await node.pipeline.scaffold(project)
    assert report.pack == "python-fastapi" and report.git_initialized
    assert {
        "pyproject.toml",
        "src/shop/main.py",
        "tests/test_health.py",
        "Dockerfile",
        "docs/DATABASE.md",
    } <= set(report.created)
    assert "docs/requirements/BRD.md" in report.documents
    assert (root / "docs/requirements/openapi.yaml").read_text(encoding="utf-8").count(
        "/orders"
    ) == 1
    estimates = (root / "docs/requirements/ESTIMATES.md").read_text(encoding="utf-8")
    assert "8 story points" in estimates
    skills = {s["name"]: s for s in report.skills}
    assert set(skills) == {"shop-architecture", "shop-standards", "shop-domain"}
    arch = (root / ".claude/skills/shop-architecture/SKILL.md").read_text(encoding="utf-8")
    assert "name: shop-architecture" in arch and "version: 1" in arch
    assert "ADR-001" in arch and "NFR-001" in arch
    domain = (root / ".claude/skills/shop-domain/SKILL.md").read_text(encoding="utf-8")
    assert "FR-001" in domain
    # Unapproved facts are excluded (AC-US033-01): the draft RULE and DATA items.
    assert "RULE-001" not in domain and "Order" not in domain
    standards = (root / ".claude/skills/shop-standards/SKILL.md").read_text(encoding="utf-8")
    assert "uv run pytest" in standards and "RULE-001" not in standards

    # Nothing changed → nothing regenerated.
    again = {s["name"]: s for s in await node.pipeline.skills(project)}
    assert not any(s["updated"] for s in again.values())
    # Approved architecture changes → new skill version (AC-US033-02).
    await node.requirements.edit(
        project.id, "NFR-001", actor="owner", body="p95 < 1 s at 500 orders/day"
    )
    await node.requirements.approve(project.id, "NFR-001", actor="owner")
    bumped = {s["name"]: s for s in await node.pipeline.skills(project)}
    assert bumped["shop-architecture"]["version"] == 2 and bumped["shop-architecture"]["updated"]
    assert bumped["shop-domain"]["version"] == 1 and not bumped["shop-domain"]["updated"]
    assert "version: 2" in (root / ".claude/skills/shop-architecture/SKILL.md").read_text(
        encoding="utf-8"
    )
    # Re-scaffolding never overwrites existing files.
    (root / "src/shop/main.py").write_text("# mine\n", encoding="utf-8")
    second = await node.pipeline.scaffold(project)
    assert "src/shop/main.py" in second.skipped and second.created == []
    assert (root / "src/shop/main.py").read_text(encoding="utf-8") == "# mine\n"


@pytest.mark.req("AC-US032-03", "US-032")
async def test_changing_approved_requirements_creates_an_impact_task_and_blocks_coding(
    node: NodeContainer,
) -> None:
    interview_id = await interviewed(node)
    await node.pipeline.complete(interview_id)
    project = await node.projects.get("shop")
    await node.requirements.approve(project.id, "BR-001", actor="owner")
    _, stale = await node.requirements.edit(project.id, "FR-001", actor="owner", body="Also PayPal")
    assert "US-001" in stale
    impact = next(j for j in await node.jobs.list() if j.kind == "impact_analysis")
    impact = await wait_for(node, impact.id, (JobStatus.WAITING_FOR_USER, JobStatus.COMPLETED))
    assert impact.status == JobStatus.WAITING_FOR_USER
    assert "US-001" in impact.status_reason
    assert [n.kind for n in node.notifications.sent][-1] == "question"

    # Implementation of stale/unapproved requirements is blocked (ADR-014).
    job = await node.runner.submit("Implement US-001 card payment", project=project)
    job = await wait_for(
        node,
        job.id,
        (JobStatus.BLOCKED, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.WAITING_FOR_APPROVAL),
    )
    assert job.status == JobStatus.BLOCKED and "US-001 is stale" in job.status_reason

    # Re-approving FR-001 cascades to its subtree; items that only *trace* to it (API, data)
    # still need a decision, so the impact task keeps waiting.
    await node.requirements.approve(project.id, "FR-001", actor="owner")
    await node.runner.resume(impact.id)
    impact = await wait_for(node, impact.id, (JobStatus.COMPLETED, JobStatus.WAITING_FOR_USER))
    assert impact.status == JobStatus.WAITING_FOR_USER
    assert "API-001" in impact.status_reason and "US-001" not in impact.status_reason
    await node.requirements.clear_stale(
        project.id,
        ["API-001", "DATA-001"],
        actor="owner",
        reason="PayPal does not change the order API",
    )
    await node.runner.resume(impact.id)
    impact = await wait_for(node, impact.id, (JobStatus.COMPLETED, JobStatus.WAITING_FOR_USER))
    assert impact.status == JobStatus.COMPLETED


@pytest.mark.req("BR-014")
async def test_estimate_uses_points_rates_and_plans_sprints(node: NodeContainer) -> None:
    interview_id = await interviewed(node)
    await node.pipeline.complete(interview_id)
    project = await node.projects.get("shop")
    est = await node.pipeline.estimate(project)
    assert est.total_points == 8 and est.total_hours == 48.0 and est.person_days == 6.0
    assert est.by_role["developer"] == {"hours": 28.8, "rate": 60.0, "cost": 1728.0}
    assert est.total_cost == round(28.8 * 60 + 12.0 * 50 + 7.2 * 55, 2)
    assert [s.stories for s in est.sprints] == [["US-001", "US-002"]]
    assert est.confidence == "high" or est.confidence == "medium"
