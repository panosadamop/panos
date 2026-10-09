import pytest
from fastapi.testclient import TestClient

from panos_core import API_VERSION, __version__
from panos_core.config import PanosSettings
from panos_core.domain.ids import is_valid_id
from panos_core.nodes import current_platform
from panos_node_api import create_app
from panos_node_api.security import AuthConfig
from tests.fakes import FakeDesktop
from tests.integration.api.conftest import BASE_URL, TOKEN


@pytest.mark.req("BR-001")
def test_health_reports_ok_with_node_identity(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["version"] == __version__
    assert body["api_version"] == API_VERSION
    assert is_valid_id(body["node_id"], "node")
    assert body["uptime_s"] >= 0


@pytest.mark.req("NFR-OBS-001")
def test_correlation_id_echoed_or_generated(client: TestClient) -> None:
    supplied = client.get("/api/v1/health", headers={"X-Correlation-ID": "client-abc-12345"})
    assert supplied.headers["x-correlation-id"] == "client-abc-12345"
    generated = client.get("/api/v1/health", headers={"X-Correlation-ID": "bad id!"})
    assert generated.headers["x-correlation-id"] != "bad id!"
    assert len(generated.headers["x-correlation-id"]) == 32


def test_capabilities_describe_node_and_features(client: TestClient) -> None:
    body = client.get("/api/v1/capabilities").json()
    assert body["api_version"] == API_VERSION
    assert body["node"]["platform"] == current_platform()
    assert {"health", "capabilities", "approvals", "audit", "projects", "apps"} <= set(
        body["features"]
    )
    assert body["desktop"]["launch"] == "supported"
    assert "shell.run" in body["tools"]
    assert "fs.write" in body["tools"]
    assert not any(t.startswith("email") for t in body["tools"])


def test_node_identity_stable_across_app_restarts(
    settings: PanosSettings, auth: AuthConfig
) -> None:

    ids = []
    for _ in range(2):
        with TestClient(
            create_app(settings, auth, desktop=FakeDesktop()),
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as c:
            ids.append(c.get("/api/v1/health").json()["node_id"])
    assert ids[0] == ids[1]


def test_unknown_route_is_404_problem(client: TestClient) -> None:
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"


def test_wrong_method_is_405_problem(client: TestClient) -> None:
    r = client.post("/api/v1/health")
    assert r.status_code == 405
    assert r.json()["status"] == 405


def test_database_failure_returns_503_without_internals(client: TestClient) -> None:
    container = client.app.state.container  # type: ignore[attr-defined]

    class BrokenEngine:
        def connect(self) -> object:
            raise RuntimeError("disk I/O error at C:\\secret\\path")

    real = container.engine
    container.engine = BrokenEngine()
    try:
        r = client.get("/api/v1/health")
    finally:
        container.engine = real
    assert r.status_code == 503
    assert "secret" not in r.text


def test_openapi_schema_is_exportable() -> None:
    from scripts.export_openapi import build_schema

    schema = build_schema()
    assert schema["info"]["title"] == "Panos Local Node API"
    assert "/api/v1/health" in schema["paths"]
