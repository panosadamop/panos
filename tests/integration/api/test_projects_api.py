"""Projects and apps over the local API (API.md: /projects, /projects/{id}/analyze...)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeDesktop
from tests.repos import copy_repo

pytestmark = pytest.mark.req("FR-004", "FR-005", "FR-003")


def _register(client: TestClient, path: Path, **extra: object) -> dict[str, object]:
    r = client.post("/api/v1/projects", json={"path": str(path), **extra})
    assert r.status_code == 201, r.text
    data: dict[str, object] = r.json()
    return data


@pytest.mark.req("US-011", "AC-US011-01", "AC-US011-03")
def test_register_list_get_and_conflicts(client: TestClient, tmp_path: Path) -> None:
    repo = copy_repo("python_fastapi", tmp_path)
    project = _register(client, repo)
    assert project["privacy_mode"] == "LOCAL_ONLY"
    listed = client.get("/api/v1/projects").json()["projects"]
    assert [p["id"] for p in listed] == [project["id"]]
    assert client.get(f"/api/v1/projects/{project['name']}").json()["id"] == project["id"]
    dup = client.post("/api/v1/projects", json={"path": str(repo)})
    assert dup.status_code == 409
    missing = client.post("/api/v1/projects", json={"path": str(tmp_path / "nope")})
    assert missing.status_code == 404
    assert client.get("/api/v1/projects/nope").status_code == 404
    assert client.post("/api/v1/projects", json={}).status_code == 422


def test_create_in_workspace(client: TestClient) -> None:
    r = client.post("/api/v1/projects", json={"name": "fresh", "create": True})
    assert r.status_code == 201
    assert Path(r.json()["path"]).is_dir()


@pytest.mark.req("US-012", "AC-US012-01")
def test_analyze_and_snapshot(client: TestClient, tmp_path: Path) -> None:
    project = _register(client, copy_repo("spring_maven", tmp_path))
    assert client.get(f"/api/v1/projects/{project['id']}/snapshot").status_code == 404
    snap = client.post(f"/api/v1/projects/{project['id']}/analyze").json()
    assert {"Spring Boot", "Vaadin"} <= set(snap["stack"]["frameworks"])
    assert snap["commands"]["test"] == [["./mvnw", "test"]]
    latest = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert latest["id"] == snap["id"]


@pytest.mark.req("AC-US005-05")
def test_privacy_change_requires_confirmation(client: TestClient, tmp_path: Path) -> None:
    project = _register(client, copy_repo("laravel", tmp_path))
    url = f"/api/v1/projects/{project['id']}"
    refused = client.patch(url, json={"privacy_mode": "STANDARD"})
    assert refused.status_code == 409
    ok = client.patch(url, json={"privacy_mode": "STANDARD", "confirm_privacy_change": True})
    assert ok.json()["privacy_mode"] == "STANDARD"


def test_policy_override_endpoint(client: TestClient, tmp_path: Path) -> None:
    project = _register(client, copy_repo("laravel", tmp_path))
    base = f"/api/v1/projects/{project['id']}/policy"
    ok = client.put(f"{base}/fs.write", json={"level": "P3"})
    assert ok.json()["policy_overrides"] == {"fs.write": "P3_APPROVAL_REQUIRED"}
    assert client.put(f"{base}/git.commit", json={"level": "P0"}).status_code == 400
    assert client.put(f"{base}/fs.write", json={"level": "P9"}).status_code == 422
    cleared = client.put(f"{base}/fs.write", json={"level": None})
    assert cleared.json()["policy_overrides"] == {}


@pytest.mark.req("FR-020")
def test_index_and_search(client: TestClient, tmp_path: Path) -> None:
    project = _register(client, copy_repo("python_fastapi", tmp_path))
    stats = client.post(f"/api/v1/projects/{project['id']}/index").json()
    assert stats["indexed"] > 0
    hits = client.get(
        f"/api/v1/projects/{project['id']}/search", params={"q": "OrderService"}
    ).json()["hits"]
    assert hits[0]["uri"] == "src/orders/api.py"
    assert hits[0]["privacy_mode"] == "LOCAL_ONLY"


@pytest.mark.req("US-010", "AC-US010-02")
def test_open_project_preview_and_launch(
    client: TestClient, desktop: FakeDesktop, tmp_path: Path
) -> None:
    client.post("/api/v1/apps/refresh")
    project = _register(client, copy_repo("python_fastapi", tmp_path))
    client.post(f"/api/v1/projects/{project['id']}/analyze")
    url = f"/api/v1/projects/{project['id']}/open"
    preview = client.post(url, json={"dry_run": True}).json()
    assert preview["app"] == "jetbrains.pycharm"
    assert desktop.launched == []
    launched = client.post(url, json={}).json()
    assert launched["status"] == "SUCCEEDED"
    assert desktop.launched[0][1] == [project["path"]]
    with_app = client.post(url, json={"app": "vscode"}).json()
    assert with_app["output"]["app"] == "vscode"


@pytest.mark.req("US-010", "AC-US010-01")
def test_apps_list_launch_and_preferences(client: TestClient, desktop: FakeDesktop) -> None:
    apps = client.post("/api/v1/apps/refresh").json()["apps"]
    assert {a["key"] for a in apps} >= {"jetbrains.pycharm", "browser.chrome"}
    r = client.post(
        "/api/v1/apps/chrome/launch", json={"target": "url", "url": "https://example.invalid"}
    )
    assert r.json()["status"] == "SUCCEEDED"
    bad = client.post("/api/v1/apps/notepad/launch", json={})
    assert bad.json()["status"] == "DENIED"
    pref = client.put("/api/v1/preferences/ide/Python", json={"app": "vscode"})
    assert pref.json()["key"] == "vscode"
    missing = client.put("/api/v1/preferences/ide/PHP", json={"app": "sublime"})
    assert missing.status_code == 404
