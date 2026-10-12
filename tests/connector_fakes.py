"""An in-memory stand-in for Google, Microsoft Graph, Jira Cloud and Azure DevOps (M8 tests).

Used through ``httpx.MockTransport``: every request a connector sends is recorded in
``FakeCloud.requests`` so tests can assert what was (and was not) called. Tokens and data
are fake; nothing leaves the machine.
"""

import base64
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

ACCESS = "ya29.fake-access"  # assembled fakes, not credentials
REFRESH = "1//fake-refresh"


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


@dataclass
class FakeCloud:
    requests: list[httpx.Request] = field(default_factory=list)
    access_token: str = ACCESS
    reject_tokens: bool = False  # 401 for every API call (forces a refresh)
    refresh_fails: bool = False
    issued: int = 0
    messages: dict[str, dict[str, Any]] = field(default_factory=dict)
    drafts: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    busy: list[dict[str, str]] = field(default_factory=list)
    drive_files: dict[str, dict[str, Any]] = field(default_factory=dict)
    graph_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: dict[str, dict[str, Any]] = field(default_factory=dict)
    work_items: dict[int, dict[str, Any]] = field(default_factory=dict)
    routes: list[tuple[str, str, Callable[[httpx.Request, re.Match[str]], httpx.Response]]] = field(
        default_factory=list
    )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def calls(self, method: str | None = None, contains: str = "") -> list[httpx.Request]:
        return [
            r
            for r in self.requests
            if (method is None or r.method == method) and contains in str(r.url)
        ]

    # ------------------------------------------------------------------ dispatch
    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        host, path = url.host, url.path
        if host == "oauth2.googleapis.com" or (
            host == "login.microsoftonline.com" and path.endswith("/token")
        ):
            return self._token(request)
        if host == "login.microsoftonline.com" and path.endswith("/devicecode"):
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-123",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://microsoft.com/devicelogin",
                    "interval": 0,
                    "expires_in": 60,
                    "message": "Enter ABCD-EFGH",
                },
            )
        auth = request.headers.get("Authorization", "")
        if self.reject_tokens and auth.startswith("Bearer"):
            return httpx.Response(401, json={"error": {"message": "invalid credentials"}})
        if auth.startswith("Bearer") and auth != f"Bearer {self.access_token}":
            return httpx.Response(401, json={"error": {"message": "stale token"}})
        for method, pattern, handler in self.routes:
            m = re.fullmatch(pattern, path)
            if m and request.method == method:
                return handler(request, m)
        if host == "openidconnect.googleapis.com":
            return httpx.Response(200, json={"email": "owner@example.com", "sub": "1"})
        if host == "graph.microsoft.com" and path == "/v1.0/me":
            return httpx.Response(200, json={"userPrincipalName": "owner@contoso.example"})
        if host.endswith("atlassian.net"):
            return self._jira(request)
        if host == "dev.azure.com":
            return self._ado(request)
        return httpx.Response(
            404, json={"error": {"message": f"fake: no route {request.method} {path}"}}
        )

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        grant = form.get("grant_type", [""])[0]
        if grant == "refresh_token" and self.refresh_fails:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if request.url.path.endswith("/revoke"):
            return httpx.Response(200)
        self.issued += 1
        self.access_token = f"{ACCESS}-{self.issued}"
        self.reject_tokens = False
        body: dict[str, Any] = {
            "access_token": self.access_token,
            "expires_in": 3600,
            "scope": "openid email",
        }
        if grant != "refresh_token":
            body["refresh_token"] = REFRESH
        return httpx.Response(200, json=body)

    # ------------------------------------------------------------------ Jira / ADO
    def _jira(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not request.headers.get("Authorization", "").startswith("Basic "):
            return httpx.Response(401)
        if path == "/rest/api/3/myself":
            return httpx.Response(200, json={"emailAddress": "owner@example.com"})
        handler = getattr(self, "jira_handler", None)
        if handler is not None:
            return handler(request)  # type: ignore[no-any-return]
        return httpx.Response(404, json={"errorMessages": ["fake: unknown"]})

    def _ado(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/_apis/connectionData"):
            return httpx.Response(200, json={"authenticatedUser": {"providerDisplayName": "Owner"}})
        handler = getattr(self, "ado_handler", None)
        if handler is not None:
            return handler(request)  # type: ignore[no-any-return]
        return httpx.Response(404, json={"message": "fake: unknown"})


def form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def body(request: httpx.Request) -> Any:
    return json.loads(request.content or b"null")


def query(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}


# ---------------------------------------------------------------------- Gmail
def gmail_message(
    mid: str, *, sender: str, subject: str, text: str = "", html_body: str = "", thread: str = ""
) -> dict[str, Any]:
    parts = []
    if text:
        parts.append({"mimeType": "text/plain", "body": {"data": b64(text)}})
    if html_body:
        parts.append({"mimeType": "text/html", "body": {"data": b64(html_body)}})
    parts.append({"mimeType": "application/pdf", "filename": "scan.pdf", "body": {}})
    return {
        "id": mid,
        "threadId": thread or f"t-{mid}",
        "snippet": (text or html_body)[:80],
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "owner@example.com"},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Fri, 9 Oct 2026 09:00:00 +0300"},
                {"name": "Message-ID", "value": f"<{mid}@mail.example>"},
            ],
            "parts": parts,
        },
    }


def install_gmail(cloud: FakeCloud) -> None:
    base = r"/gmail/v1/users/me"

    def listing(request: httpx.Request, m: re.Match[str]) -> httpx.Response:
        q = query(request).get("q", "").lower()
        hits = [
            {"id": i, "threadId": msg["threadId"]}
            for i, msg in cloud.messages.items()
            if not q or q in json.dumps(msg).lower() or q.startswith("in:")
        ]
        return httpx.Response(200, json={"messages": hits})

    def get(request: httpx.Request, m: re.Match[str]) -> httpx.Response:
        msg = cloud.messages.get(m.group(1))
        return httpx.Response(200, json=msg) if msg else httpx.Response(404)

    def thread(request: httpx.Request, m: re.Match[str]) -> httpx.Response:
        msgs = [x for x in cloud.messages.values() if x["threadId"] == m.group(1)]
        return httpx.Response(200, json={"id": m.group(1), "messages": msgs})

    def create(request: httpx.Request, m: re.Match[str]) -> httpx.Response:
        draft_id = m.group(1) if m.groups() and m.group(1) else f"r-{len(cloud.drafts) + 1}"
        message = body(request)["message"]
        cloud.drafts[draft_id] = message
        return httpx.Response(
            200,
            json={
                "id": draft_id,
                "message": {"id": f"m-{draft_id}", "threadId": message.get("threadId", "t-new")},
            },
        )

    cloud.routes += [
        ("GET", rf"{base}/messages", listing),
        ("GET", rf"{base}/messages/([A-Za-z0-9_-]+)", get),
        ("GET", rf"{base}/threads/([A-Za-z0-9_-]+)", thread),
        ("POST", rf"{base}/drafts", create),
        ("PUT", rf"{base}/drafts/([A-Za-z0-9_-]+)", create),
    ]


def decoded_draft(message: dict[str, Any]) -> str:
    raw = message["raw"]
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()


# ---------------------------------------------------------------------- Google Calendar
def calendar_event(
    eid: str,
    summary: str,
    start: str,
    end: str,
    *,
    me: str | None = "accepted",
    transparent: bool = False,
    all_day: bool = False,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": eid,
        "summary": summary,
        "status": "confirmed",
        "start": {"date": start[:10]} if all_day else {"dateTime": start},
        "end": {"date": end[:10]} if all_day else {"dateTime": end},
        "organizer": {"email": "boss@client.example"},
        "htmlLink": f"https://calendar.google.com/event?eid={eid}",
    }
    if me is not None:
        event["attendees"] = [
            {"email": "owner@example.com", "self": True, "responseStatus": me},
            {"email": "boss@client.example", "responseStatus": "accepted"},
        ]
    if transparent:
        event["transparency"] = "transparent"
    return event


def install_calendar(cloud: FakeCloud) -> None:
    base = r"/calendar/v3/calendars/([^/]+)/events"

    def listing(request: httpx.Request, m: re.Match[str]) -> httpx.Response:
        return httpx.Response(200, json={"items": list(cloud.events.values())})

    def get(request: httpx.Request, m: re.Match[str]) -> httpx.Response:
        event = cloud.events.get(m.group(2))
        return httpx.Response(200, json=event) if event else httpx.Response(404)

    def create(request: httpx.Request, m: re.Match[str]) -> httpx.Response:
        event = {
            "id": f"new{len(cloud.events) + 1}",
            "htmlLink": "https://cal/new",
            **body(request),
        }
        cloud.events[event["id"]] = event
        return httpx.Response(200, json=event)

    def patch(request: httpx.Request, m: re.Match[str]) -> httpx.Response:
        event = cloud.events[m.group(2)]
        event.update(body(request))
        return httpx.Response(200, json=event)

    def delete(request: httpx.Request, m: re.Match[str]) -> httpx.Response:
        cloud.events.pop(m.group(2), None)
        return httpx.Response(204)

    def freebusy(request: httpx.Request, m: re.Match[str]) -> httpx.Response:
        items = body(request)["items"]
        return httpx.Response(
            200, json={"calendars": {i["id"]: {"busy": cloud.busy} for i in items}}
        )

    cloud.routes += [
        ("GET", base, listing),
        ("POST", base, create),
        ("GET", rf"{base}/([^/]+)", get),
        ("PATCH", rf"{base}/([^/]+)", patch),
        ("DELETE", rf"{base}/([^/]+)", delete),
        ("POST", r"/calendar/v3/freeBusy", freebusy),
    ]
