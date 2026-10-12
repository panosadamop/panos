"""Effort and analytics over the node API and CLI (M10-02/05/06)."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from panos_cli.main import app
from tests.integration.api.test_cli_ai import cli  # noqa: F401 - fixture

pytestmark = pytest.mark.req("BR-014", "BR-015")

runner = CliRunner()


def setup_project(client: TestClient, tmp_path: Path, stories: int = 5) -> str:
    repo = tmp_path / "shop"
    repo.mkdir()
    assert client.post("/api/v1/projects", json={"path": str(repo)}).status_code == 201
    for i in range(stories):
        r = client.post(
            "/api/v1/projects/shop/requirements",
            json={"type": "US", "title": f"Story {i + 1}", "attributes": {"points": 3}},
        )
        assert r.status_code == 201, r.text
    return "shop"


def test_effort_round_trip_over_the_api(client: TestClient, tmp_path: Path) -> None:
    setup_project(client, tmp_path)
    base = client.post("/api/v1/projects/shop/estimate/baseline").json()
    assert base["total_hours"] == 5 * 3 * 6.0
    for i in range(1, 6):
        r = client.post(
            "/api/v1/projects/shop/effort",
            json={"requirement_key": f"US-00{i}", "hours": 9, "completes": True},
        )
        assert r.status_code == 201 and r.json()["status"] == "APPROVED"
    bad = client.post(
        "/api/v1/projects/shop/effort", json={"requirement_key": "US-077", "hours": 1}
    )
    assert bad.status_code == 404
    report = client.get("/api/v1/projects/shop/effort/report").json()
    assert report["logged_hours"] == 45 and len(report["finished"]) == 5
    [suggestion] = client.post("/api/v1/effort/calibrations/suggest").json()
    assert suggestion["hours_per_point"] == 3.0 and suggestion["status"] == "SUGGESTED"
    decided = client.post(
        f"/api/v1/effort/calibrations/{suggestion['id']}/decision", json={"decision": "approve"}
    )
    assert decided.json()["status"] == "APPROVED"
    after = client.get("/api/v1/projects/shop/estimate").json()
    assert after["total_hours"] == 45.0 and any("calibrated" in a for a in after["assumptions"])
    assert (
        client.post("/api/v1/effort/act_missing/decision", json={"decision": "approve"}).status_code
        == 404
    )
    analytics = client.get("/api/v1/ai/analytics", params={"days": 7}).json()
    assert {"calls", "cost_usd", "by_model", "budget", "latency_ms", "daily"} <= set(analytics)


@pytest.mark.usefixtures("cli")
def test_effort_and_analytics_cli(client: TestClient, tmp_path: Path) -> None:
    setup_project(client, tmp_path, stories=1)
    logged = runner.invoke(app, ["effort", "log", "US-001", "1h30m", "-p", "shop", "--done"])
    assert logged.exit_code == 0, logged.output
    assert "US-001" in logged.stdout and "1.5 h developer (finished)" in logged.stdout
    listed = runner.invoke(app, ["effort", "list", "-p", "shop"])
    assert "APPROVED" in listed.stdout
    assert runner.invoke(app, ["effort", "log", "US-001", "lots", "-p", "shop"]).exit_code != 0
    suggest = runner.invoke(app, ["effort", "suggest"])
    assert "Not enough finished stories" in suggest.stdout
    report = runner.invoke(app, ["effort", "report", "-p", "shop"])
    assert "1.5 h logged" in report.stdout
    out = runner.invoke(app, ["ai", "analytics", "--json"]).stdout
    data: dict[str, Any] = json.loads(out[out.index("{\n") :])
    assert "by_model" in data
