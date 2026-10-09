"""/jobs over the local API and ``panos job`` (M5-01, M5-04, M5-10)."""

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from panos_cli.main import app
from tests.ai_fakes import MockAI, ScriptedAgents
from tests.integration.api.test_cli_ai import cli  # noqa: F401 - fixture
from tests.repos import copy_repo

pytestmark = pytest.mark.req("FR-022", "US-050", "US-052")

runner = CliRunner()
SETTLED = {"COMPLETED", "BLOCKED", "FAILED", "CANCELLED", "WAITING_FOR_APPROVAL", "PAUSED"}


@pytest.fixture
def agents() -> ScriptedAgents:
    return ScriptedAgents()


@pytest.fixture
def mock_ai(agents: ScriptedAgents) -> MockAI:
    ai = MockAI()
    ai.respond(agents)
    return ai


def register_calc(client: TestClient, tmp_path: Path) -> dict[str, Any]:
    root = copy_repo("calc_unittest", tmp_path, git=True)
    r = client.post("/api/v1/projects", json={"path": str(root)})
    assert r.status_code == 201, r.text
    data: dict[str, Any] = r.json()
    return data


def wait(client: TestClient, job_id: str, limit_s: float = 60) -> dict[str, Any]:
    deadline = time.monotonic() + limit_s
    while True:
        job: dict[str, Any] = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in SETTLED and not job["running"]:
            return job
        assert time.monotonic() < deadline, job
        time.sleep(0.1)


@pytest.mark.req("AC-US020-04", "AC-US050-01", "US-023", "AC-US023-01", "FR-024")
def test_job_lifecycle_over_the_api(client: TestClient, tmp_path: Path) -> None:
    project = register_calc(client, tmp_path)
    r = client.post(
        "/api/v1/jobs", json={"objective": "Add multiply to calc", "project": project["name"]}
    )
    assert r.status_code == 202, r.text
    job = wait(client, r.json()["id"])
    assert job["status"] == "WAITING_FOR_APPROVAL" and job["pending_approval"]
    assert job["phase"] == "commit" and job["checkpoints"]
    assert {c["path"] for c in job["result"]["changed_files"]} == {
        "calc.py",
        "tests/test_multiply.py",
    }
    report_job = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert any(s["agent"] == "agent:planner" for s in report_job["steps"])
    assert client.get("/api/v1/jobs").json()["jobs"][0]["id"] == job["id"]
    assert client.get("/api/v1/jobs", params={"status": "COMPLETED"}).json()["jobs"] == []
    events = client.get(f"/api/v1/jobs/{job['id']}/events").json()
    assert {"job.created", "checkpoint.created", "approval.requested"} <= {
        e["event_type"] for e in events
    }

    # Rolling back the waiting job restores the files; the commit is then rejected.
    rb = client.post(f"/api/v1/jobs/{job['id']}/rollback", json={})
    assert rb.status_code == 200 and rb.json()["verified"]
    assert "multiply" not in (Path(project["path"]) / "calc.py").read_text(encoding="utf-8")
    cancel = client.post(f"/api/v1/jobs/{job['id']}/cancel")
    assert cancel.status_code == 200 and cancel.json()["status"] == "CANCELLED"
    assert client.post(f"/api/v1/jobs/{job['id']}/resume").status_code == 409


def test_job_api_errors(client: TestClient, tmp_path: Path) -> None:
    assert client.get("/api/v1/jobs/job_missing").status_code == 404
    assert client.post("/api/v1/jobs", json={"objective": "do it"}).status_code == 400
    assert (
        client.post("/api/v1/jobs", json={"objective": "x y z", "project": "nope"}).status_code
        == 404
    )
    assert (
        client.post("/api/v1/jobs", json={"objective": "x y z", "kind": "command"}).status_code
        == 400
    )
    assert (
        client.post(
            "/api/v1/jobs", json={"objective": "x y z", "kind": "weird", "argv": ["a"]}
        ).status_code
        == 400
    )
    assert client.post("/api/v1/jobs/job_missing/pause").status_code == 404


@pytest.mark.req("FR-002", "NFR-PERF-002")
def test_work_requests_in_conversation_start_jobs(client: TestClient, tmp_path: Path) -> None:
    project = register_calc(client, tmp_path)
    reply = client.post(
        "/api/v1/conversation/messages",
        json={"text": "implement a multiply function", "project": project["id"]},
    )
    assert reply.status_code == 200
    text = reply.json()["text"]
    assert text.startswith("Started job job_")
    job_id = text.split()[2]
    assert wait(client, job_id)["status"] == "WAITING_FOR_APPROVAL"


@pytest.mark.usefixtures("cli")
@pytest.mark.req("AC-US052-01", "US-040", "AC-US040-02")
def test_cli_open_project_implement_test_and_request_commit(
    client: TestClient, tmp_path: Path, agents: ScriptedAgents
) -> None:
    """TEST_STRATEGY e2e#1 (in-process): CLI → project → analyze → implement → tests →
    commit approval requested, not executed."""
    root = copy_repo("calc_unittest", tmp_path, git=True)
    assert runner.invoke(app, ["project", "add", str(root)]).exit_code == 0
    assert runner.invoke(app, ["project", "open", "calc_unittest"]).exit_code == 0
    started = runner.invoke(app, ["job", "start", "-p", "calc_unittest", "Add", "multiply"])
    assert started.exit_code == 0, started.output
    job_id = started.stdout.split("Started ")[1].split()[0]
    watched = runner.invoke(app, ["job", "watch", job_id, "--interval", "0.2"])
    assert "WAITING_FOR_APPROVAL" in watched.stdout
    assert "panos approvals approve apr_" in watched.stdout
    shown = runner.invoke(app, ["job", "show", job_id])
    assert "ok   test: python manage.py test" in shown.stdout
    assert "M calc.py" in shown.stdout and "A tests/test_multiply.py" in shown.stdout
    # Not committed: still only the fixture's initial commit.
    import subprocess

    count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert count == "1"
    assert "WAITING_FOR_APPROVAL" in runner.invoke(app, ["job", "list"]).stdout
    paused = runner.invoke(app, ["job", "pause", job_id])
    assert "PAUSED" in paused.stdout
    assert runner.invoke(app, ["job", "events", job_id]).exit_code == 0
    rolled = runner.invoke(app, ["job", "rollback", job_id])
    assert "verified: True" in rolled.stdout
    assert "CANCELLED" in runner.invoke(app, ["job", "cancel", job_id]).stdout
    assert agents.roles()[:3] == ["planner", "developer", "reviewer"]
