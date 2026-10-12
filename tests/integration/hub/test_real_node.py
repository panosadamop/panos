"""A real node joins the hub; the hub dispatches a job and approves it remotely (M9-04/05).

The node is the full node API (in-process); the job runs through the node's own runner,
policy and approval service (AC-US061-03). The hub only relays (ADR-028 §5–6).
"""

import asyncio
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from panos_control_plane import nodes
from panos_control_plane.db.orm import ApprovalMirror, JobMirror
from panos_control_plane.server import RunningHub
from panos_core.config import PanosSettings
from panos_node_api import create_app
from panos_node_api.container import AIOverride
from panos_node_api.security import AuthConfig
from tests.ai_fakes import MockAI
from tests.fakes import FakeDesktop
from tests.integration.hub.conftest import admin, node_settings

pytestmark = pytest.mark.req("BR-024", "FR-026", "US-061", "AC-US061-03")

TOKEN = "node-token-" + "y" * 32
PORT = 48124


@pytest.fixture
def node(settings: PanosSettings, tmp_path: Path) -> Iterator[TestClient]:
    ns = node_settings(settings, tmp_path, "nodeA")
    ai = MockAI()
    app = create_app(
        ns,
        AuthConfig.for_loopback(TOKEN, PORT, ["tauri://localhost"]),
        desktop=FakeDesktop(),
        ai=AIOverride(dict(ai.providers), ai.models),
    )
    with TestClient(
        app, base_url=f"http://127.0.0.1:{PORT}", headers={"Authorization": f"Bearer {TOKEN}"}
    ) as client:
        yield client


async def test_dispatch_runs_under_the_target_policy_and_is_approved_from_the_hub(
    running: RunningHub, node: TestClient, tmp_path: Path
) -> None:
    code, _ = await nodes.create_code(running.state, "enroll", label="nodeA")
    joined = await asyncio.to_thread(
        node.post,
        "/api/v1/remote/join",
        json={
            "hub_url": f"https://localhost:{running.api_port}",
            "code": code,
            "fingerprint": running.state.material.fingerprint,
            "name": "laptop",
        },
    )
    assert joined.status_code == 200, joined.text

    async def connected() -> Any:
        s = (await asyncio.to_thread(node.get, "/api/v1/remote")).json()
        return s["link"] == "connected" and s

    status = await _await(connected)
    assert status["name"] == "laptop" and status["joined"]

    repo = tmp_path / "shop"
    repo.mkdir()
    (repo / "hello.py").write_bytes(b"print('hello from the laptop')\n")
    created = await asyncio.to_thread(node.post, "/api/v1/projects", json={"path": str(repo)})
    assert created.status_code == 201

    async with admin(running) as hub:
        # The inventory reaches the hub (names and privacy only).
        async def has_project() -> Any:
            listed = (await hub.get("/nodes")).json()
            return listed and listed[0]["projects"]

        projects = await _await(has_project)
        assert projects[0]["name"] == "shop" and projects[0]["privacy_mode"] == "LOCAL_ONLY"
        sent = await hub.post(
            "/dispatch",
            json={"node": "laptop", "objective": "Run hello", "project": "shop", "kind": "command",
                  "argv": [sys.executable, "hello.py"]},
        )  # fmt: skip
        assert sent.json()["state"] == "accepted", sent.text
        job_id = sent.json()["job_id"]

        # Running repository code needs approval on the target; the request is mirrored.
        async def pending() -> Any:
            rows = (await hub.get("/approvals")).json()
            return [a for a in rows if a["job_id"] == job_id]

        [approval] = await _await(pending)
        assert approval["summary"] is None  # LOCAL_ONLY: not stored on the hub
        live = (await hub.get(f"/approvals/{approval['approval_id']}")).json()["live"]
        assert "hello.py" in live["summary"]  # shown live from the node
        decided = await hub.post(
            f"/approvals/{approval['approval_id']}/decision", json={"decision": "approve"}
        )
        assert decided.status_code == 200, decided.text

        async def finished() -> Any:
            jobs = (await hub.get("/jobs")).json()
            return [j for j in jobs if j["job_id"] == job_id and j["status"] == "COMPLETED"]

        [job] = await _await(finished, 60)
        assert job["dispatch_id"] == sent.json()["id"] and job["privacy_mode"] == "LOCAL_ONLY"
        assert all(s["summary"] is None for s in job["steps"])
        detail = (await hub.get(f"/jobs/{job_id}")).json()
        assert "hello from the laptop" in str(detail["live"]["result"]) or detail["live"]["result"]

    # The node's own audit shows who decided, and that the dispatch came from the hub.
    audit = (await asyncio.to_thread(node.get, "/api/v1/audit", params={"limit": 200})).json()[
        "events"
    ]
    decided_by = [e["actor"] for e in audit if e["event_type"] == "approval.decided"]
    assert decided_by == ["hub-admin@hub"]
    assert any(e["event_type"] == "remote.dispatch_accepted" for e in audit)
    async with running.state.db() as s:
        stored = await s.get(ApprovalMirror, approval["approval_id"])
        mirrored = await s.get(JobMirror, job_id)
    assert stored is not None and stored.status == "APPROVED"
    assert mirrored is not None and mirrored.status == "COMPLETED"

    async with admin(running) as hub:
        revoked = await asyncio.wait_for(hub.post("/nodes/laptop/revoke"), 30)
        assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"

    async def link_revoked() -> Any:
        return (await asyncio.to_thread(node.get, "/api/v1/remote")).json()["link"] == "revoked"

    await _await(link_revoked)
    left = await asyncio.to_thread(node.post, "/api/v1/remote/leave")
    assert left.json()["joined"] is False and left.json()["link"] == "disabled"


async def _await(fn: Any, limit_s: float = 30.0) -> Any:
    deadline = time.monotonic() + limit_s
    while True:
        value = await fn()
        if value:
            return value
        assert time.monotonic() < deadline, "condition not met in time"
        await asyncio.sleep(0.2)
