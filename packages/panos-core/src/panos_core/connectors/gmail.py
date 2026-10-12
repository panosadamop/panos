"""Gmail: search, read and drafts only (FR-017, BR-019, M8-01).

There is deliberately no method that sends, deletes, trashes, labels or forwards mail, and
the endpoint allowlist would reject such calls anyway. Message content is returned as
untrusted data with the account's privacy label.
"""

import base64
import html
import re
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import Any, Final

from panos_core.connectors.http import ConnectorHttp

API: Final = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_BODY_CHARS: Final = 20_000
_SUMMARY_HEADERS: Final = ["From", "To", "Cc", "Subject", "Date", "Message-ID"]


@dataclass(frozen=True)
class MailSummary:
    id: str
    thread_id: str
    sender: str
    to: str
    subject: str
    date: str
    snippet: str
    labels: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MailMessage:
    id: str
    thread_id: str
    sender: str
    to: str
    cc: str
    subject: str
    date: str
    message_id: str
    body: str
    attachments: list[str]
    labels: list[str]


@dataclass(frozen=True)
class Draft:
    id: str
    message_id: str
    thread_id: str | None


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _Text()
    parser.feed(markup)
    text = html.unescape("".join(parser.parts)).replace("\xa0", " ")
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


def _walk(part: dict[str, Any]) -> list[dict[str, Any]]:
    out = [part]
    for child in part.get("parts", []) or []:
        out += _walk(child)
    return out


def body_text(payload: dict[str, Any]) -> tuple[str, list[str]]:
    """Best text body (text/plain, else text/html converted) and attachment names."""
    parts = _walk(payload)
    attachments = [p["filename"] for p in parts if p.get("filename")]
    plain = [p for p in parts if p.get("mimeType") == "text/plain" and not p.get("filename")]
    rich = [p for p in parts if p.get("mimeType") == "text/html" and not p.get("filename")]
    for candidates, convert in ((plain, False), (rich, True)):
        for p in candidates:
            data = (p.get("body") or {}).get("data")
            if data:
                text = _decode(data)
                return (html_to_text(text) if convert else text)[:MAX_BODY_CHARS], attachments
    return "", attachments


def _addresses(values: list[str]) -> str:
    return ", ".join(f"{n} <{a}>" if n else a for n, a in getaddresses(values) if a)


class GmailClient:
    def __init__(self, http: ConnectorHttp) -> None:
        self._http = http

    async def search(self, query: str, *, limit: int = 20) -> list[MailSummary]:
        listing = await self._http.request(
            "GET", f"{API}/messages", params={"q": query, "maxResults": min(limit, 50)}
        )
        out = []
        for ref in (listing or {}).get("messages", [])[:limit]:
            msg = await self._http.request(
                "GET",
                f"{API}/messages/{ref['id']}",
                params={"format": "metadata", "metadataHeaders": _SUMMARY_HEADERS},
            )
            h = _headers(msg.get("payload", {}))
            out.append(
                MailSummary(
                    id=msg["id"],
                    thread_id=msg.get("threadId", ""),
                    sender=h.get("from", ""),
                    to=h.get("to", ""),
                    subject=h.get("subject", ""),
                    date=h.get("date", ""),
                    snippet=html.unescape(msg.get("snippet", "")),
                    labels=list(msg.get("labelIds", [])),
                )
            )
        return out

    async def message(self, message_id: str) -> MailMessage:
        msg = await self._http.request(
            "GET", f"{API}/messages/{message_id}", params={"format": "full"}
        )
        payload = msg.get("payload", {})
        h = _headers(payload)
        body, attachments = body_text(payload)
        return MailMessage(
            id=msg["id"],
            thread_id=msg.get("threadId", ""),
            sender=h.get("from", ""),
            to=h.get("to", ""),
            cc=h.get("cc", ""),
            subject=h.get("subject", ""),
            date=h.get("date", ""),
            message_id=h.get("message-id", ""),
            body=body,
            attachments=attachments,
            labels=list(msg.get("labelIds", [])),
        )

    async def thread(self, thread_id: str) -> list[MailMessage]:
        data = await self._http.request(
            "GET", f"{API}/threads/{thread_id}", params={"format": "full"}
        )
        out = []
        for msg in (data or {}).get("messages", []):
            payload = msg.get("payload", {})
            h = _headers(payload)
            body, attachments = body_text(payload)
            out.append(
                MailMessage(
                    id=msg["id"],
                    thread_id=thread_id,
                    sender=h.get("from", ""),
                    to=h.get("to", ""),
                    cc=h.get("cc", ""),
                    subject=h.get("subject", ""),
                    date=h.get("date", ""),
                    message_id=h.get("message-id", ""),
                    body=body,
                    attachments=attachments,
                    labels=list(msg.get("labelIds", [])),
                )
            )
        return out

    async def save_draft(
        self,
        *,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        reply_to: MailMessage | None = None,
        draft_id: str | None = None,
    ) -> Draft:
        """Create (or update) a draft in the user's Drafts folder. Never sends it."""
        email = EmailMessage()
        email["To"] = _addresses(to)
        if cc:
            email["Cc"] = _addresses(cc)
        email["Subject"] = subject
        if reply_to is not None and reply_to.message_id:
            email["In-Reply-To"] = reply_to.message_id
            email["References"] = reply_to.message_id
        email.set_content(body)
        raw = base64.urlsafe_b64encode(email.as_bytes()).decode().rstrip("=")
        message: dict[str, Any] = {"raw": raw}
        if reply_to is not None:
            message["threadId"] = reply_to.thread_id
        if draft_id:
            data = await self._http.request(
                "PUT", f"{API}/drafts/{draft_id}", json={"id": draft_id, "message": message}
            )
        else:
            data = await self._http.request("POST", f"{API}/drafts", json={"message": message})
        msg = (data or {}).get("message", {})
        return Draft(id=data["id"], message_id=msg.get("id", ""), thread_id=msg.get("threadId"))
