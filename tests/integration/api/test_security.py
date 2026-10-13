"""Loopback API protection (ADR-003)."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.integration.api.conftest import ORIGIN, PORT, TOKEN

pytestmark = pytest.mark.req("NFR-SEC-001")

PROBLEM = "application/problem+json"


def test_missing_token_is_401_problem(anon_client: TestClient) -> None:
    r = anon_client.get("/api/v1/health")
    assert r.status_code == 401
    assert r.headers["content-type"] == PROBLEM
    assert r.headers["www-authenticate"] == "Bearer"
    body = r.json()
    assert body["status"] == 401
    assert "correlation_id" in body


@pytest.mark.parametrize(
    "header", [f"Bearer {TOKEN}x", f"bearer {TOKEN}", TOKEN, "Bearer ", f"Basic {TOKEN}"]
)
def test_wrong_token_is_401(anon_client: TestClient, header: str) -> None:
    assert anon_client.get("/api/v1/health", headers={"Authorization": header}).status_code == 401


@pytest.mark.parametrize(
    "host",
    [
        "evil.example:48123",
        f"127.0.0.1:{PORT + 1}",
        "localhost",
        f"attacker.localhost:{PORT}",
        f"0.0.0.0:{PORT}",
    ],
)
def test_foreign_host_header_rejected_even_with_token(client: TestClient, host: str) -> None:
    """DNS-rebinding defense: the token alone is not enough."""
    r = client.get("/api/v1/health", headers={"Host": host})
    assert r.status_code == 400
    assert r.headers["content-type"] == PROBLEM


def test_localhost_host_header_accepted(client: TestClient) -> None:
    assert client.get("/api/v1/health", headers={"Host": f"localhost:{PORT}"}).status_code == 200


def test_disallowed_origin_rejected(client: TestClient) -> None:
    r = client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_allowed_origin_accepted(client: TestClient) -> None:
    assert client.get("/api/v1/health", headers={"Origin": ORIGIN}).status_code == 200


def test_preflight_from_allowed_origin_needs_no_token(anon_client: TestClient) -> None:
    """Browsers send CORS preflights without credentials; the WebView's origin differs."""
    r = anon_client.options(
        "/api/v1/projects",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert "authorization" in r.headers["access-control-allow-headers"]
    assert "POST" in r.headers["access-control-allow-methods"]
    assert r.content == b""


def test_preflight_from_foreign_origin_rejected(anon_client: TestClient) -> None:
    r = anon_client.options(
        "/api/v1/projects",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert r.status_code == 403
    assert "access-control-allow-origin" not in r.headers


def test_options_without_preflight_headers_still_needs_token(anon_client: TestClient) -> None:
    assert anon_client.options("/api/v1/projects", headers={"Origin": ORIGIN}).status_code == 401


def test_responses_to_allowed_origin_carry_exact_cors_origin(
    client: TestClient, anon_client: TestClient
) -> None:
    ok = client.get("/api/v1/health", headers={"Origin": ORIGIN})
    assert ok.headers["access-control-allow-origin"] == ORIGIN
    assert ok.headers["vary"] == "Origin"
    denied = anon_client.get("/api/v1/health", headers={"Origin": ORIGIN})
    assert denied.status_code == 401
    assert denied.headers["access-control-allow-origin"] == ORIGIN  # the app can read the 401
    assert "access-control-allow-origin" not in client.get("/api/v1/health").headers


def test_unknown_routes_also_require_auth(anon_client: TestClient) -> None:
    assert anon_client.get("/api/v1/does-not-exist").status_code == 401


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_interactive_docs_and_live_schema_disabled(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


def test_no_shell_or_exec_endpoints_exist(client: TestClient) -> None:
    paths = set(client.app.openapi()["paths"])  # type: ignore[attr-defined]
    assert "/api/v1/health" in paths
    # Tools are invoked only by the orchestrator, never directly over the API (API.md).
    forbidden = ("shell", "exec", "command", "run", "tool")
    assert not any(word in p for p in paths for word in forbidden)


def test_websocket_with_foreign_origin_is_closed(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as excinfo,
        client.websocket_connect("/ws/anything", headers={"Origin": "https://evil.example"}),
    ):
        pass
    assert excinfo.value.code == 1008
