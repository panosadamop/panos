"""Requirements API and CLI, including TEST_STRATEGY e2e#2 in-process (M6-10/11)."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from panos_cli.main import app
from tests.ai_fakes import MockAI, ScriptedAgents
from tests.fakes import FakeDesktop
from tests.integration.api.test_cli_ai import cli  # noqa: F401 - fixture

pytestmark = pytest.mark.req("FR-007", "FR-008", "FR-009", "FR-010")

runner = CliRunner()
BRIEF = "We need an online shop where customers buy our handmade products."


@pytest.fixture
def agents() -> ScriptedAgents:
    return ScriptedAgents()


@pytest.fixture
def mock_ai(agents: ScriptedAgents) -> MockAI:
    ai = MockAI()
    ai.respond(agents)
    return ai


def post(client: TestClient, path: str, body: dict[str, Any], code: int = 200) -> Any:
    r = client.post(f"/api/v1{path}", json=body)
    assert r.status_code == code, r.text
    return r.json()


def test_requirements_api_flow(client: TestClient) -> None:
    itv = post(client, "/interviews", {"brief": BRIEF, "name": "shop"}, 201)
    assert itv["status"] == "OPEN" and len(itv["questions"]) == 3
    for q in itv["questions"]:
        itv = post(
            client,
            f"/interviews/{itv['id']}/answers",
            {"question_id": q["id"], "answer": "yes" if q["id"] != "q3" else None},
        )
    assert itv["status"] == "READY"
    assert client.get(f"/api/v1/interviews/{itv['id']}").json()["questions"][2]["skipped"]
    items = post(client, f"/interviews/{itv['id']}/complete", {})
    assert {"BR-001", "FR-001", "US-001", "ADR-001"} <= {i["key"] for i in items}

    reqs = client.get("/api/v1/projects/shop/requirements", params={"type": "FR"}).json()
    assert [r["key"] for r in reqs] == ["FR-001", "FR-002"]
    created = post(
        client,
        "/projects/shop/requirements",
        {"type": "US", "title": "As a customer I can track my order", "parent_key": "FR-001"},
        201,
    )
    assert created["key"] == "US-003"
    approvals = post(
        client,
        "/projects/shop/requirements/BR-001/approval",
        {"decision": "approve", "exclude": ["FR-002"], "comments": "v1"},
    )
    assert approvals[0]["key"] == "BR-001" and approvals[0]["excluded_keys"] == ["FR-002"]
    assert all(a["cascaded_from_id"] == approvals[0]["id"] for a in approvals[1:])
    edited = client.patch(
        "/api/v1/projects/shop/requirements/FR-001", json={"body": "card and PayPal"}
    ).json()
    assert edited["requirement"]["version"] == 2 and "US-001" in edited["stale"]
    detail = client.get("/api/v1/projects/shop/requirements/FR-001").json()
    assert [h["status"] for h in detail["history"]] == ["APPROVED", "DRAFT"]
    assert detail["approvals"][0]["decided_by"] == "user"
    trace = client.get("/api/v1/projects/shop/traceability").json()
    assert trace["roots"][0]["key"] == "BR-001" and "US-001" in trace["stale"]
    post(
        client,
        "/projects/shop/links",
        {"source_key": "TC-002", "target": "AC-US001-02", "relation": "verifies"},
        201,
    )
    assert (
        client.request(
            "DELETE",
            "/api/v1/projects/shop/links",
            json={"source_key": "TC-002", "target": "AC-US001-02", "relation": "verifies"},
        ).status_code
        == 204
    )
    cleared = post(
        client,
        "/projects/shop/requirements/clear-stale",
        {"keys": ["API-001"], "reason": "unchanged contract"},
    )
    assert cleared["cleared"] == ["API-001"]
    rejected = post(
        client,
        "/projects/shop/requirements/FR-002/approval",
        {"decision": "reject", "comments": "later"},
    )
    assert rejected[0]["decision"] == "REJECTED"
    est = client.get("/api/v1/projects/shop/estimate").json()
    assert est["total_points"] == 11 and est["currency"] == "EUR"


def test_requirements_api_errors(client: TestClient, mock_ai: MockAI) -> None:
    assert client.post("/api/v1/interviews", json={"brief": BRIEF}).status_code == 400
    assert client.get("/api/v1/interviews/itv_" + "0" * 32).status_code == 404
    assert client.get("/api/v1/projects/nope/requirements").status_code == 404
    mock_ai.local.available = False  # LOCAL_ONLY project and no local model: explained
    r = client.post("/api/v1/interviews", json={"brief": BRIEF, "name": "offline"})
    assert r.status_code == 503 and r.json()["reason"] == "no_route"
    mock_ai.local.available = True
    client.get("/api/v1/ai/providers", params={"refresh": True})
    # Nothing was left behind by the failed attempt: the same name works now.
    post(client, "/interviews", {"brief": BRIEF, "name": "offline"}, 201)
    itv = post(client, "/interviews", {"brief": BRIEF, "name": "shop2"}, 201)
    r = client.post(f"/api/v1/interviews/{itv['id']}/complete", json={})
    assert r.status_code == 409  # questions still open
    assert client.post("/api/v1/projects/shop2/scaffold", json={}).status_code == 409
    assert (
        client.post(
            "/api/v1/projects/shop2/requirements", json={"type": "AC", "title": "orphan"}
        ).status_code
        == 400
    )
    assert client.get("/api/v1/projects/shop2/requirements/FR-404").status_code == 404


@pytest.mark.usefixtures("cli")
@pytest.mark.req("US-030", "US-031", "US-033", "AC-US031-01", "BR-013")
def test_cli_brief_to_scaffolded_project_with_skills_opened_in_ide(
    client: TestClient, desktop: FakeDesktop, settings: Any
) -> None:
    """TEST_STRATEGY e2e#2 (in-process): brief → interview → approved spec/backlog →
    scaffold → skills → open IDE."""
    started = runner.invoke(app, ["new", "-n", "shop", *BRIEF.split()])
    assert started.exit_code == 0, started.output
    assert "q1. Which payment methods" in started.stdout
    interview_id = started.stdout.split("Interview ")[1].split()[0]
    assert (
        runner.invoke(app, ["interview", "answer", interview_id, "q1", "Card", "only"]).exit_code
        == 0
    )
    assert runner.invoke(app, ["interview", "answer", interview_id, "q2", "500/day"]).exit_code == 0
    last = runner.invoke(app, ["interview", "answer", interview_id, "q3", "--skip"])
    assert "All answered" in last.stdout
    done = runner.invoke(app, ["interview", "complete", interview_id])
    assert done.exit_code == 0 and "ADR-001" in done.stdout

    listed = runner.invoke(app, ["req", "list", "-p", "shop", "--type", "US"])
    assert "US-001" in listed.stdout and "DRAFT" in listed.stdout
    approved = runner.invoke(app, ["req", "approve", "-p", "shop", "BR-001"])
    assert "Approved" in approved.stdout and "AC-US001-01" in approved.stdout
    assert "Approved 1" in runner.invoke(app, ["req", "approve", "-p", "shop", "ADR-001"]).stdout
    trace = runner.invoke(app, ["req", "trace", "-p", "shop"])
    assert "BR-001 [APPROVED]" in trace.stdout and "gap: AC-US001-02 has no TC" in trace.stdout
    estimate = runner.invoke(app, ["project", "estimate", "shop"])
    assert "8 points" in estimate.stdout and "sprint 1: US-001, US-002" in estimate.stdout
    scaffolded = runner.invoke(app, ["project", "scaffold", "shop", "--open"])
    assert scaffolded.exit_code == 0, scaffolded.output
    assert "Stack python-fastapi" in scaffolded.stdout
    assert "shop-architecture v1" in scaffolded.stdout
    assert "Open in IDE: SUCCEEDED" in scaffolded.stdout
    root = Path(settings.workspace_dir) / "shop"
    assert (root / "src" / "shop" / "main.py").is_file()
    assert (root / ".claude" / "skills" / "shop-domain" / "SKILL.md").is_file()
    assert (root / "docs" / "requirements" / "BACKLOG.md").is_file()
    exe, args = desktop.launched[-1]
    assert "pycharm" in exe and str(root.resolve()) in args  # preferred IDE for the stack
    shown = runner.invoke(app, ["req", "show", "-p", "shop", "US-001"])
    assert "(cascaded)" in shown.stdout and "answer:" in shown.stdout
