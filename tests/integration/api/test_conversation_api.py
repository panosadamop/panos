"""Conversation, AI and event-stream endpoints over the local API, with mock models."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from panos_core.ai.types import ChatRequest
from panos_core.config import PanosSettings
from panos_core.config.settings import ProviderSettings
from panos_node_api.routers import events as events_router
from tests.ai_fakes import CLOUD_CHAT, LOCAL_CHAT, MockAI
from tests.fakes import FakeDesktop
from tests.integration.api.conftest import PORT, TOKEN
from tests.repos import copy_repo

pytestmark = pytest.mark.req("FR-002")

MSG = "/api/v1/conversation/messages"
WS = f"ws://127.0.0.1:{PORT}/api/v1/ws/events"


def register(client: TestClient, tmp_path: Path, privacy: str = "LOCAL_ONLY") -> dict[str, Any]:
    r = client.post("/api/v1/projects", json={"path": str(copy_repo("python_fastapi", tmp_path))})
    assert r.status_code == 201, r.text
    project: dict[str, Any] = r.json()
    if privacy != "LOCAL_ONLY":
        project = set_privacy(client, project["id"], privacy)
    return project


def set_privacy(client: TestClient, ref: str, privacy: str) -> dict[str, Any]:
    r = client.patch(
        f"/api/v1/projects/{ref}", json={"privacy_mode": privacy, "confirm_privacy_change": True}
    )
    assert r.status_code == 200, r.text
    data: dict[str, Any] = r.json()
    return data


def say(client: TestClient, text: str, **kw: Any) -> dict[str, Any]:
    r = client.post(MSG, json={"text": text, **kw})
    assert r.status_code == 200, r.text
    data: dict[str, Any] = r.json()
    return data


def all_text(request: ChatRequest) -> str:
    return "\n".join(m.content for m in request.messages)


@pytest.mark.req("NFR-PERF-002")
def test_fast_path_commands_need_no_model(
    client: TestClient, tmp_path: Path, mock_ai: MockAI, desktop: FakeDesktop
) -> None:
    project = register(client, tmp_path)
    listed = say(client, "list projects")
    assert listed["intent"] == "list_projects" and project["name"] in listed["text"]
    assert listed["provider"] is None and listed["locality"] == "local"
    opened = say(client, f"άνοιξε το έργο {project['name']}")
    assert opened["intent"] == "open_project" and opened["tool"]["status"] == "SUCCEEDED"
    launched = say(client, "open vscode")
    assert launched["tool"]["status"] == "SUCCEEDED"
    assert [exe for exe, _ in desktop.launched][-1].endswith("vscode/bin/app.exe")
    assert say(client, "status")["text"].startswith("Panos is running")
    assert "Visual Studio Code" in say(client, "show installed apps")["text"]
    assert mock_ai.local.calls == [] and mock_ai.cloud.calls == []


@pytest.mark.req("BR-004", "US-005", "AC-US005-02", "NFR-SEC-005", "FR-020")
def test_local_only_project_chat_uses_local_model_with_retrieval(
    client: TestClient, tmp_path: Path, mock_ai: MockAI
) -> None:
    project = register(client, tmp_path)
    stats = client.post(f"/api/v1/projects/{project['id']}/index?semantic=true").json()
    assert stats["chunks"] > 0
    assert mock_ai.local.embed_calls and mock_ai.cloud.embed_calls == []
    reply = say(client, "What does OrderService do?", project=project["name"])
    assert reply["privacy"] == "LOCAL_ONLY" and reply["locality"] == "local"
    assert reply["model"] == LOCAL_CHAT
    assert any(s.startswith("src/orders/api.py") for s in reply["sources"])
    sent, _ = mock_ai.local.calls[-1]
    assert "<untrusted" in all_text(sent) and "OrderService" in all_text(sent)
    assert mock_ai.cloud.calls == []
    conversation = client.get(f"/api/v1/conversations/{reply['conversation_id']}").json()
    assert [m["role"] for m in conversation["messages"]] == ["user", "assistant"]
    assert {m["privacy"] for m in conversation["messages"]} == {"LOCAL_ONLY"}
    assert conversation["project_id"] == project["id"]


@pytest.mark.req("NFR-SEC-004", "BR-003")
def test_standard_project_uses_cloud_with_redaction(
    client: TestClient, tmp_path: Path, mock_ai: MockAI
) -> None:
    project = register(client, tmp_path, "STANDARD")
    secret = "ghp_" + "A1b2C3d4" * 5  # assembled at runtime: not a real token
    reply = say(client, f"Why does login fail with token {secret}?", project=project["id"])
    assert reply["locality"] == "cloud" and reply["model"] == CLOUD_CHAT
    sent, _ = mock_ai.cloud.calls[-1]
    assert sent.redacted and secret not in all_text(sent)
    usage = client.get("/api/v1/ai/usage").json()
    assert usage["rows"][0]["provider"] == "cloud" and usage["total_cost_usd"] > 0
    audit = client.get("/api/v1/audit", params={"event_type": "ai.cloud_call"}).json()
    assert len(audit["events"]) == 1


@pytest.mark.req("BR-004", "AC-US005-02", "NFR-SEC-005")
def test_conversation_privacy_is_sticky(
    client: TestClient, tmp_path: Path, mock_ai: MockAI
) -> None:
    project = register(client, tmp_path, "STANDARD")
    first = say(client, "hello", project=project["id"])
    assert first["locality"] == "cloud" and first["privacy"] == "STANDARD"
    cid = first["conversation_id"]
    set_privacy(client, project["id"], "LOCAL_ONLY")
    second = say(client, "and now?", conversation_id=cid)
    assert second["privacy"] == "LOCAL_ONLY" and second["locality"] == "local"
    set_privacy(client, project["id"], "STANDARD")  # relaxing the project does not untaint
    third = say(client, "still local?", conversation_id=cid)
    assert third["privacy"] == "LOCAL_ONLY" and third["locality"] == "local"
    assert len(mock_ai.cloud.calls) == 1  # only the very first turn
    # A fresh conversation in the (now STANDARD) project may use the cloud again.
    assert say(client, "new topic", project=project["id"])["locality"] == "cloud"
    # The tainted history is never sent to the cloud with the new conversation.
    assert "and now?" not in all_text(mock_ai.cloud.calls[-1][0])


@pytest.mark.req("AC-US005-04")
def test_no_local_model_gives_explained_503_and_never_uses_cloud(
    client: TestClient, mock_ai: MockAI
) -> None:
    mock_ai.local.available = False
    r = client.post(MSG, json={"text": "summarize my notes"})  # unscoped = LOCAL_ONLY
    assert r.status_code == 503
    body = r.json()
    assert body["reason"] == "no_route" and "local-only" in body["detail"]
    assert any("LOCAL_ONLY" in item["reason"] for item in body["rejected"])
    assert mock_ai.cloud.calls == []


@pytest.mark.req("BR-027", "AC-US024-02")
def test_cloud_down_falls_back_to_local(
    client: TestClient, tmp_path: Path, mock_ai: MockAI
) -> None:
    project = register(client, tmp_path, "STANDARD")
    mock_ai.cloud.available = False
    client.get("/api/v1/ai/providers", params={"refresh": True})
    assert say(client, "hi", project=project["id"])["locality"] == "local"


def test_streaming_ndjson(client: TestClient, mock_ai: MockAI) -> None:
    mock_ai.respond(lambda r, m: "Καλημέρα από το Panos")
    with client.stream("POST", MSG, json={"text": "καλημέρα", "stream": True}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in r.iter_lines() if line]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[1] == "route" and kinds[-1] == "reply"
    text = "".join(e["text"] for e in events if e["type"] == "text")
    assert text.strip() == "Καλημέρα από το Panos"
    assert events[-1]["reply"]["text"] == "Καλημέρα από το Panos"


def test_streaming_errors_are_events(client: TestClient, mock_ai: MockAI) -> None:
    mock_ai.local.available = False
    with client.stream("POST", MSG, json={"text": "hi", "stream": True}) as r:
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert events[-1]["type"] == "error" and events[-1]["error"]["reason"] == "no_route"
    bad = client.post(MSG, json={"text": "hi", "stream": True, "conversation_id": "cnv_nope"})
    assert bad.status_code == 404


def test_conversation_errors(client: TestClient, tmp_path: Path) -> None:
    assert client.post(MSG, json={"text": ""}).status_code == 422
    assert client.post(MSG, json={"text": "   "}).status_code == 400
    assert client.post(MSG, json={"text": "hi", "project": "nope"}).status_code == 404
    assert client.get("/api/v1/conversations/cnv_nope").status_code == 404
    project = register(client, tmp_path)
    other = client.post("/api/v1/projects", json={"name": "other", "create": True}).json()
    cid = say(client, "hi", project=project["id"])["conversation_id"]
    r = client.post(MSG, json={"text": "x", "conversation_id": cid, "project": other["id"]})
    assert r.status_code == 409
    assert len(client.get("/api/v1/conversations").json()) == 1


@pytest.mark.req("FR-021", "AC-US005-04")
def test_ai_endpoints(client: TestClient) -> None:
    providers = client.get("/api/v1/ai/providers").json()
    assert {(p["id"], p["locality"], p["ok"]) for p in providers} == {
        ("local", "local", True),
        ("cloud", "cloud", True),
    }
    models = client.get("/api/v1/ai/models").json()
    assert {m["name"] for m in models} >= {LOCAL_CHAT, CLOUD_CHAT}
    local = client.post("/api/v1/ai/route", json={"text": "x", "privacy": "LOCAL_ONLY"}).json()
    assert local["locality"] == "local"
    assert any("LOCAL_ONLY" in r["reason"] for r in local["rejected"])
    standard = client.post("/api/v1/ai/route", json={"privacy": "STANDARD"}).json()
    assert standard["locality"] == "cloud" and standard["reasons"]
    assert client.get("/api/v1/ai/usage", params={"days": 7}).json()["rows"] == []


@pytest.mark.req("NFR-SEC-003", "NFR-SEC-004")
def test_api_keys_are_stored_in_secret_store_and_audited_without_value(
    client: TestClient, settings: PanosSettings
) -> None:
    key = "sk-" + "t" * 30  # assembled at runtime: not a real key
    assert client.put("/api/v1/ai/providers/nope/key", json={"key": key}).status_code == 404
    # Providers come from the AI override in tests; "configured" means present in settings.
    settings.ai.providers.append(ProviderSettings(id="anthropic", kind="anthropic"))
    assert client.put("/api/v1/ai/providers/anthropic/key", json={"key": key}).status_code == 204
    container = client.app.state.container  # type: ignore[attr-defined]
    assert container.secrets.get("ai.anthropic.api_key") == key
    events = client.get("/api/v1/audit", params={"event_type": "ai.key_set"}).json()["events"]
    assert len(events) == 1 and key not in json.dumps(events)
    assert client.delete("/api/v1/ai/providers/anthropic/key").status_code == 204
    assert container.secrets.get("ai.anthropic.api_key") is None


def test_ws_events_require_first_message_auth(client: TestClient) -> None:
    with client.websocket_connect(WS) as ws:
        ws.send_json({"type": "auth", "token": "wrong"})
        with pytest.raises(WebSocketDisconnect) as err:
            ws.receive_json()
    assert err.value.code == 1008
    with client.websocket_connect(WS) as ws:
        ws.send_text("not json")
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_ws_auth_timeout(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events_router, "AUTH_TIMEOUT_S", 0.2)
    with client.websocket_connect(WS) as ws, pytest.raises(WebSocketDisconnect):
        ws.receive_json()


def test_ws_events_stream_conversation_events(client: TestClient) -> None:
    with client.websocket_connect(WS) as ws:
        ws.send_json({"type": "auth", "token": TOKEN})
        ok = ws.receive_json()
        assert ok["type"] == "auth.ok" and ok["v"] == 1 and ok["epoch"]
        reply = say(client, "list projects")
        first = ws.receive_json()
        second = ws.receive_json()
    assert first["type"] == second["type"] == "conversation.message"
    assert first["v"] == 1 and first["payload"]["role"] == "user"
    assert second["payload"]["message_id"] == reply["message_id"]


def test_ws_events_carry_streamed_tokens(client: TestClient, mock_ai: MockAI) -> None:
    mock_ai.respond(lambda r, m: "alpha beta")
    with client.websocket_connect(WS) as ws:
        ws.send_json({"type": "auth", "token": TOKEN})
        ws.receive_json()
        with client.stream("POST", MSG, json={"text": "hi", "stream": True}) as r:
            list(r.iter_lines())
        received = [ws.receive_json() for _ in range(4)]  # user msg, 2 deltas, reply
    deltas = [e["payload"]["text"] for e in received if e["type"] == "conversation.delta"]
    assert "".join(deltas).strip() == "alpha beta"
