"""Connector foundation (M8-00, ADR-026): flows, token storage, refresh and the allowlist."""

import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from panos_core.connectors import allowlists
from panos_core.connectors.http import (
    AuthExpiredError,
    ConnectorHttp,
    EndpointNotAllowedError,
)
from panos_core.connectors.service import ConnectorError
from panos_core.domain.connectors import ConnectorKind, ConnectorStatus
from panos_core.domain.enums import PrivacyMode
from tests.connector_fakes import REFRESH, form
from tests.integration.connectors.conftest import Env

pytestmark = pytest.mark.req("NFR-SEC-004", "NFR-SEC-002")

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"


@pytest.mark.req("BR-019", "AC-US043-02")
@pytest.mark.parametrize(
    ("method", "url", "allowed"),
    [
        ("GET", f"{GMAIL}/messages", True),
        ("GET", f"{GMAIL}/messages/18c2", True),
        ("POST", f"{GMAIL}/drafts", True),
        ("PUT", f"{GMAIL}/drafts/r-123", True),
        ("POST", f"{GMAIL}/messages/send", False),
        ("POST", f"{GMAIL}/drafts/send", False),
        ("PUT", f"{GMAIL}/drafts/send", False),
        ("POST", "https://www.googleapis.com/upload/gmail/v1/users/me/messages/send", False),
        ("POST", f"{GMAIL}/messages/18c2/trash", False),
        ("DELETE", f"{GMAIL}/messages/18c2", False),
        ("POST", f"{GMAIL}/messages/18c2/modify", False),
        ("POST", f"{GMAIL}/settings/forwardingAddresses", False),
        ("GET", "http://gmail.googleapis.com/gmail/v1/users/me/messages", False),  # not https
        ("GET", "https://evil.example/gmail/v1/users/me/messages", False),
        ("DELETE", "https://www.googleapis.com/drive/v3/files/abc", False),
    ],
)
def test_google_allowlist_has_no_send_or_destructive_endpoints(
    method: str, url: str, allowed: bool
) -> None:
    http = ConnectorHttp(allowlists.for_account("google", None))
    assert http.allowed(method, url) is allowed


def test_tracker_allowlists_are_bound_to_the_account_and_have_no_delete() -> None:
    jira = ConnectorHttp(allowlists.jira("https://acme.atlassian.net"))
    assert jira.allowed("GET", "https://acme.atlassian.net/rest/api/3/issue/SHOP-12")
    assert not jira.allowed("GET", "https://other.atlassian.net/rest/api/3/issue/SHOP-12")
    assert not jira.allowed("DELETE", "https://acme.atlassian.net/rest/api/3/issue/SHOP-12")
    ado = ConnectorHttp(allowlists.azure_devops("https://dev.azure.com/acme"))
    assert ado.allowed("PATCH", "https://dev.azure.com/acme/Shop/_apis/wit/workitems/42")
    assert not ado.allowed("PATCH", "https://dev.azure.com/other/Shop/_apis/wit/workitems/42")
    assert not ado.allowed("DELETE", "https://dev.azure.com/acme/Shop/_apis/wit/workitems/42")


async def test_blocked_endpoint_is_never_sent(env: Env) -> None:
    http = ConnectorHttp(allowlists.GMAIL, transport=env.cloud.transport())
    with pytest.raises(EndpointNotAllowedError):
        await http.request("POST", f"{GMAIL}/messages/send", json={"raw": "x"})
    assert env.cloud.requests == []


async def test_google_loopback_flow_stores_tokens_only_in_the_keyring(env: Env) -> None:
    with pytest.raises(ConnectorError, match="google-client"):
        await env.service.start_google(["mail"], open_browser=False)
    env.service.set_client(ConnectorKind.GOOGLE, "cid.apps.googleusercontent.com", "csecret")
    flow = await env.service.start_google(["mail", "calendar"])
    assert env.opened == [flow.auth_url]
    assert flow.auth_url is not None
    params = {k: v[0] for k, v in parse_qs(urlsplit(flow.auth_url).query).items()}
    assert params["code_challenge_method"] == "S256" and params["access_type"] == "offline"
    assert "gmail.compose" in params["scope"] and "calendar.events" in params["scope"]
    redirect = params["redirect_uri"]
    assert redirect.startswith("http://127.0.0.1:")

    async with httpx.AsyncClient() as browser:  # the user's browser following the redirect
        wrong = await browser.get(redirect, params={"code": "c", "state": "forged"})
        assert "could not complete" in wrong.text
    assert flow.task is not None
    await flow.task
    assert flow.status == "failed" and "state mismatch" in (flow.error or "")

    flow = await env.service.start_google(["mail"], open_browser=False)
    params = {k: v[0] for k, v in parse_qs(urlsplit(flow.auth_url or "").query).items()}
    async with httpx.AsyncClient() as browser:
        ok = await browser.get(
            params["redirect_uri"], params={"code": "auth-code", "state": params["state"]}
        )
        assert "connected" in ok.text
    assert flow.task is not None
    await flow.task
    assert flow.status == "connected", flow.error
    exchange = form(env.cloud.calls("POST", "oauth2.googleapis.com/token")[-1])
    assert exchange["code"] == "auth-code" and len(exchange["code_verifier"]) >= 43

    [account] = await env.service.accounts()
    assert (account.label, account.scopes) == ("owner@example.com", ["mail"])
    assert account.privacy_mode == PrivacyMode.LOCAL_ONLY  # connector content stays local
    stored = json.loads(env.secrets.get(account.secret_name) or "{}")
    assert stored["refresh_token"] == REFRESH
    events = await env.audit.list(limit=50)
    dumped = json.dumps([e.model_dump(mode="json") for e in events])
    assert "connector.connected" in dumped
    assert REFRESH not in dumped and env.cloud.access_token not in dumped
    assert "csecret" not in dumped


async def test_expired_tokens_are_refreshed_and_failed_refresh_needs_reauth(env: Env) -> None:
    env.service.set_client(ConnectorKind.GOOGLE, "cid", "csecret")
    flow = await env.service.start_google(["mail"], open_browser=False)
    params = {k: v[0] for k, v in parse_qs(urlsplit(flow.auth_url or "").query).items()}
    async with httpx.AsyncClient() as browser:
        await browser.get(params["redirect_uri"], params={"code": "c", "state": params["state"]})
    assert flow.task is not None
    await flow.task
    account = await env.service.get("google")
    client = env.service.client(account)
    env.cloud.reject_tokens = True  # the server says the token is no longer valid
    env.cloud.routes.append(
        ("GET", r"/gmail/v1/users/me/profile", lambda r, m: httpx.Response(200, json={"ok": 1}))
    )
    assert await client.request("GET", f"{GMAIL}/profile") == {"ok": 1}
    assert len(env.cloud.calls("POST", "/token")) == 2  # exchange + one refresh
    stored = json.loads(env.secrets.get(account.secret_name) or "{}")
    assert stored["access_token"] == env.cloud.access_token
    assert stored["refresh_token"] == REFRESH  # kept although Google omits it on refresh

    env.cloud.reject_tokens = True
    env.cloud.refresh_fails = True
    with pytest.raises(AuthExpiredError, match="reconnect"):
        await client.request("GET", f"{GMAIL}/profile")
    assert (await env.service.get(account.id)).status == ConnectorStatus.NEEDS_REAUTH
    await client.aclose()


async def test_microsoft_device_code_flow(env: Env) -> None:
    env.service.set_client(ConnectorKind.MICROSOFT, "00000000-app", tenant="organizations")
    flow = await env.service.start_microsoft()
    assert (flow.user_code, flow.verification_uri) == (
        "ABCD-EFGH",
        "https://microsoft.com/devicelogin",
    )
    assert flow.task is not None
    await flow.task
    assert flow.status == "connected", flow.error
    account = await env.service.get("microsoft")
    assert account.label == "owner@contoso.example"
    assert env.cloud.calls("POST", "/organizations/oauth2/v2.0/devicecode")


async def test_jira_and_azure_devops_tokens_are_verified_and_kept_secret(env: Env) -> None:
    jira = await env.service.connect_jira("acme.atlassian.net", "owner@example.com", "jira-tok")
    assert jira.base_url == "https://acme.atlassian.net"
    assert jira.label == "owner@example.com @ acme.atlassian.net"
    ado = await env.service.connect_azure_devops("https://dev.azure.com/acme/", "ado-pat")
    assert ado.base_url == "https://dev.azure.com/acme" and ado.label.startswith("Owner @")
    with pytest.raises(ConnectorError, match="https"):
        await env.service.connect_jira("http://acme.atlassian.net", "a", "b")
    async with env.uow_factory() as uow:
        rows = await uow.connectors.accounts()
    assert "jira-tok" not in repr(rows) and "ado-pat" not in repr(rows)

    await env.service.set_privacy(jira.id, PrivacyMode.STANDARD)
    assert (await env.service.get(jira.id)).privacy_mode == PrivacyMode.STANDARD
    await env.service.disconnect(jira.id)
    assert env.secrets.get(jira.secret_name) is None
    with pytest.raises(ConnectorError):
        await env.service.get(jira.id)
