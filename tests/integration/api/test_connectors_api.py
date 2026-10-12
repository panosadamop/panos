"""Connectors API and ``panos connect`` CLI (M8-00, ADR-026) against the fake cloud."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from panos_cli.main import app
from panos_core.config import PanosSettings
from panos_core.connectors.service import GOOGLE_FEATURES
from panos_node_api import create_app
from panos_node_api.container import AIOverride
from panos_node_api.routers.connectors import GoogleFeature
from panos_node_api.security import AuthConfig
from tests.ai_fakes import MockAI
from tests.connector_fakes import (
    FakeCloud,
    calendar_event,
    gmail_message,
    install_calendar,
    install_gmail,
)
from tests.fakes import FakeDesktop
from tests.integration.api.conftest import BASE_URL, TOKEN
from tests.integration.api.test_cli_ai import cli  # noqa: F401 - fixture
from tests.integration.api.test_research_api import FakeBrowser

pytestmark = pytest.mark.req("BR-016", "BR-019", "BR-020", "NFR-SEC-002", "NFR-PRV-003")

runner = CliRunner()
JIRA_TOKEN = "jira-api-token-secret"  # assembled fake


@pytest.fixture
def cloud() -> FakeCloud:
    return FakeCloud()


@pytest.fixture
def client(
    settings: PanosSettings,
    auth: AuthConfig,
    desktop: FakeDesktop,
    mock_ai: MockAI,
    cloud: FakeCloud,
) -> Iterator[TestClient]:
    ai = AIOverride(dict(mock_ai.providers), mock_ai.models)
    application = create_app(
        settings,
        auth,
        desktop=desktop,
        ai=ai,
        connector_transport=cloud.transport(),
        browser=FakeBrowser({"https://energy.example/crete": "8,000 kWh a year."}),
    )
    with TestClient(
        application, base_url=BASE_URL, headers={"Authorization": f"Bearer {TOKEN}"}
    ) as c:
        yield c


def test_google_features_match_the_service() -> None:
    assert set(get_args(GoogleFeature)) == set(GOOGLE_FEATURES)


def test_connect_list_privacy_and_disconnect_never_return_secrets(client: TestClient) -> None:
    api = "/api/v1/connectors"
    assert client.get(api).json() == []
    assert client.get(f"{api}/clients/google").json()["registered"] is False
    no_client = client.post(f"{api}/google", json={"features": ["mail"], "open_browser": False})
    assert no_client.status_code == 409 and "connectors-setup" in no_client.text

    reg = client.put(f"{api}/clients/google", json={"client_id": "cid-123", "client_secret": "s3"})
    assert reg.json() == {"kind": "google", "registered": True, "client_id": "cid-123",
                          "tenant": "common"}  # fmt: skip
    assert "s3" not in reg.text

    flow = client.post(f"{api}/google", json={"features": ["calendar"], "open_browser": False})
    assert flow.status_code == 202, flow.text
    params = {k: v[0] for k, v in parse_qs(urlsplit(flow.json()["auth_url"]).query).items()}
    assert params["code_challenge_method"] == "S256" and "gmail" not in params["scope"]
    with httpx.Client() as browser:  # the user's browser hits the loopback redirect
        browser.get(params["redirect_uri"], params={"code": "c", "state": params["state"]})
    for _ in range(100):
        view = client.get(f"{api}/flows/{flow.json()['id']}").json()
        if view["status"] != "pending":
            break
    assert view["status"] == "connected", view

    jira = client.post(
        f"{api}/jira",
        json={
            "site_url": "acme.atlassian.net",
            "email": "owner@example.com",
            "api_token": JIRA_TOKEN,
        },
    )
    assert jira.status_code == 201, jira.text
    accounts = client.get(api).json()
    assert {a["kind"] for a in accounts} == {"google", "jira"}
    assert all(a["privacy_mode"] == "LOCAL_ONLY" for a in accounts)
    assert JIRA_TOKEN not in json.dumps(accounts)

    relaxed = client.patch(f"{api}/jira", json={"privacy_mode": "STANDARD"})
    assert relaxed.json()["privacy_mode"] == "STANDARD"
    assert client.patch(f"{api}/jira", json={"privacy_mode": "PUBLIC"}).status_code == 422
    assert client.delete(f"{api}/jira").status_code == 204
    assert client.delete(f"{api}/jira").status_code == 404
    assert client.get(f"{api}/flows/flow_nope").status_code == 404

    audit = client.get("/api/v1/audit", params={"limit": 200}).text
    assert "connector.client_registered" in audit and "connector.disconnected" in audit
    assert JIRA_TOKEN not in audit and 's3"' not in audit


@pytest.mark.usefixtures("cli")
def test_cli_connects_with_hidden_prompts(cloud: FakeCloud) -> None:
    empty = runner.invoke(app, ["connect", "list"])
    assert empty.exit_code == 0 and "No connected accounts" in empty.stdout
    jira = runner.invoke(
        app,
        ["connect", "jira", "acme.atlassian.net", "--email", "owner@example.com"],
        input=f"{JIRA_TOKEN}\n",
    )
    assert jira.exit_code == 0, jira.output
    assert "Connected:" in jira.stdout and JIRA_TOKEN not in jira.stdout
    assert cloud.calls("GET", "/rest/api/3/myself")
    ado = runner.invoke(
        app, ["connect", "azure-devops", "https://dev.azure.com/acme"], input="ado-pat-123\n"
    )
    assert ado.exit_code == 0, ado.output
    listed = runner.invoke(app, ["connect", "list"]).stdout
    assert "jira" in listed and "azure_devops" in listed and "LOCAL_ONLY" in listed
    privacy = runner.invoke(app, ["connect", "privacy", "jira", "standard"])
    assert privacy.exit_code == 0 and "STANDARD" in privacy.stdout
    removed = runner.invoke(app, ["connect", "remove", "azure_devops"])
    assert removed.exit_code == 0
    missing = runner.invoke(app, ["connect", "remove", "azure_devops"])
    assert missing.exit_code != 0 and "404" in missing.output


@pytest.mark.req("FR-019", "FR-020", "BR-012")
def test_collections_api_and_ranked_search(client: TestClient, tmp_path: Path) -> None:
    folder = tmp_path / "company"
    folder.mkdir()
    (folder / "travel.md").write_text("Travel expenses need a receipt.", encoding="utf-8")
    api = "/api/v1/knowledge"
    created = client.post(
        f"{api}/collections",
        json={"name": "company", "kind": "folder", "location": {"path": str(folder)}},
    )
    assert created.status_code == 201, created.text
    assert created.json()["privacy_mode"] == "LOCAL_ONLY"
    bad = client.post(
        f"{api}/collections",
        json={"name": "x", "kind": "folder", "location": {"path": str(tmp_path / "nope")}},
    )
    assert bad.status_code == 400
    stats = client.post(f"{api}/collections/company/index").json()
    assert stats["indexed"] == 1 and stats["skipped"] == []
    [hit] = client.get(f"{api}/search", params={"q": "receipt"}).json()
    assert hit["source"] == "company" and hit["tier"] == 2 and hit["uri"] == "travel.md"
    assert hit["privacy_mode"] == "LOCAL_ONLY" and hit["content_hash"]

    # Conversation retrieval includes company knowledge, so the turn stays local (ADR-008).
    reply = client.post("/api/v1/conversation/messages", json={"text": "travel receipt?"})
    assert reply.status_code == 200, reply.text
    assert "travel.md#L1-L1" in reply.json()["sources"]
    assert reply.json()["locality"] == "local"

    off = client.patch(f"{api}/collections/company", json={"enabled": False}).json()
    assert off["enabled"] is False
    assert client.get(f"{api}/search", params={"q": "receipt"}).json() == []
    assert client.delete(f"{api}/collections/company").status_code == 204
    assert client.post(f"{api}/collections/company/index").status_code == 404


@pytest.mark.usefixtures("cli")
@pytest.mark.req("FR-019", "FR-020")
def test_knowledge_cli(tmp_path: Path) -> None:
    folder = tmp_path / "wiki"
    folder.mkdir()
    (folder / "vpn.md").write_text("Connect to the VPN before deploying.", encoding="utf-8")
    added = runner.invoke(app, ["knowledge", "add-folder", "wiki", str(folder)])
    assert added.exit_code == 0, added.output
    indexed = runner.invoke(app, ["knowledge", "index", "wiki"])
    assert indexed.exit_code == 0 and "1 indexed" in indexed.stdout
    found = runner.invoke(app, ["knowledge", "search", "vpn"])
    assert "[wiki] vpn.md#L1-L1" in found.stdout
    listed = runner.invoke(app, ["knowledge", "list"]).stdout
    assert "wiki" in listed and "LOCAL_ONLY" in listed
    changed = runner.invoke(app, ["knowledge", "set", "wiki", "--privacy", "standard"])
    assert "STANDARD" in changed.stdout
    assert runner.invoke(app, ["knowledge", "remove", "wiki"]).exit_code == 0


def _connect_google(client: TestClient, features: list[str]) -> None:
    api = "/api/v1/connectors"
    client.put(f"{api}/clients/google", json={"client_id": "cid-123", "client_secret": "s3"})
    flow = client.post(f"{api}/google", json={"features": features, "open_browser": False})
    params = {k: v[0] for k, v in parse_qs(urlsplit(flow.json()["auth_url"]).query).items()}
    with httpx.Client() as browser:
        browser.get(params["redirect_uri"], params={"code": "c", "state": params["state"]})
    for _ in range(100):
        if client.get(f"{api}/flows/{flow.json()['id']}").json()["status"] != "pending":
            return


@pytest.mark.req("BR-001", "FR-002", "FR-017", "FR-018", "FR-016")
def test_mail_calendar_and_research_from_chat(
    client: TestClient, cloud: FakeCloud, mock_ai: MockAI
) -> None:
    def say(text: str) -> dict[str, Any]:
        r = client.post("/api/v1/conversation/messages", json={"text": text})
        assert r.status_code == 200, r.text
        data: dict[str, Any] = r.json()
        return data

    not_connected = say("check my email")
    assert not_connected["intent"] == "inbox" and "panos connect" in not_connected["text"]

    install_gmail(cloud)
    install_calendar(cloud)
    cloud.messages["m1"] = gmail_message(
        "m1", sender="Maria <maria@client.example>", subject="Invoice 42", text="Please pay."
    )
    cloud.events["a"] = calendar_event(
        "a", "Standup", "2026-10-12T09:00:00+03:00", "2026-10-12T09:30:00+03:00"
    )
    cloud.events["b"] = calendar_event(
        "b", "Client call", "2026-10-12T09:15:00+03:00", "2026-10-12T10:00:00+03:00"
    )
    _connect_google(client, ["mail", "calendar"])

    inbox = say("έχω νέα μηνύματα;")
    assert inbox["intent"] == "inbox" and inbox["locality"] == "local"
    assert "Maria <maria@client.example>: Invoice 42" in inbox["text"]
    assert inbox["privacy"] == "LOCAL_ONLY"  # mail content keeps its label
    agenda = say("what do I have today?")
    assert agenda["intent"] == "agenda"
    assert "09:00–09:30 Standup" in agenda["text"]
    assert "Conflict: Standup overlaps Client call." in agenda["text"]
    assert mock_ai.local.calls == mock_ai.cloud.calls == []  # answered without a model

    research = say("research solar output in Crete")
    assert research["intent"] == "research" and "Started research job" in research["text"]
