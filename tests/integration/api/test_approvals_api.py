"""Approvals and audit over the local API (API.md)."""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.req("FR-010", "FR-028")


def _request_approval(client: TestClient) -> str:
    container = client.app.state.container  # type: ignore[attr-defined]
    approval = client.portal.call(  # type: ignore[union-attr]
        lambda: container.approvals.request(
            action_type="docker.exec",
            target="/proj",
            payload={"argv": ["docker", "run", "x"]},
            summary="Run container x",
            actor="agent:devops",
        )
    )
    return str(approval.id)


def test_list_and_approve_returns_single_use_token(client: TestClient) -> None:
    approval_id = _request_approval(client)
    listed = client.get("/api/v1/approvals").json()["approvals"]
    assert [a["id"] for a in listed] == [approval_id]
    assert listed[0]["summary"] == "Run container x"

    r = client.post(f"/api/v1/approvals/{approval_id}/decision", json={"decision": "approve"})
    assert r.status_code == 200
    body = r.json()
    assert body["approval"]["status"] == "APPROVED"
    assert body["token"].startswith(approval_id + ".")
    assert client.get("/api/v1/approvals").json()["approvals"] == []


def test_reject_returns_no_token_and_cannot_decide_twice(client: TestClient) -> None:
    approval_id = _request_approval(client)
    r = client.post(f"/api/v1/approvals/{approval_id}/decision", json={"decision": "reject"})
    assert r.json()["token"] is None
    again = client.post(f"/api/v1/approvals/{approval_id}/decision", json={"decision": "approve"})
    assert again.status_code == 409
    assert again.headers["content-type"] == "application/problem+json"


def test_unknown_approval_and_bad_body(client: TestClient) -> None:
    missing = client.post(
        "/api/v1/approvals/apr_" + "0" * 32 + "/decision", json={"decision": "approve"}
    )
    assert missing.status_code == 404
    bad = client.post("/api/v1/approvals/x/decision", json={"decision": "maybe"})
    assert bad.status_code == 422
    assert client.get("/api/v1/approvals/apr_" + "1" * 32).status_code == 404


def test_audit_list_and_verify(client: TestClient) -> None:
    approval_id = _request_approval(client)
    client.post(f"/api/v1/approvals/{approval_id}/decision", json={"decision": "approve"})
    events = client.get("/api/v1/audit", params={"limit": 10}).json()["events"]
    # Startup app discovery audits concurrently, so look at approval events only.
    approval_events = [e["event_type"] for e in events if e["event_type"].startswith("approval.")]
    assert approval_events[:2] == ["approval.decided", "approval.requested"]
    assert all(len(e["hash"]) == 64 for e in events)
    filtered = client.get("/api/v1/audit", params={"event_type": "approval.decided"}).json()
    assert len(filtered["events"]) == 1
    verify = client.get("/api/v1/audit/verify").json()
    assert verify["ok"] is True and verify["checked"] >= 2


def test_approval_endpoints_require_auth(anon_client: TestClient) -> None:
    assert anon_client.get("/api/v1/approvals").status_code == 401
    assert (
        anon_client.post("/api/v1/approvals/x/decision", json={"decision": "approve"}).status_code
        == 401
    )
