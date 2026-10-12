"""Endpoint allowlists per connector (ADR-026).

Only the operations Panos needs are listed. Notably absent, by design:
* Gmail: ``messages.send``, ``drafts.send``, ``messages.insert/import``, ``trash``,
  ``delete``, ``modify`` and settings/forwarding endpoints (BR-019: never send email,
  AC-US043-02/03). Drafts can be created and updated, nothing else is written.
* Calendar: writes exist (create/patch/delete an event) but the tools only call them with an
  approval of the exact operation (P3, AC-US044-02).
* Drive / OneDrive / SharePoint: read-only.
* Jira / Azure DevOps: no delete; writes only through approved change sets (AC-US042-02).
"""

import re
from typing import Final
from urllib.parse import urlsplit

from panos_core.connectors.http import Endpoint

_ID: Final = r"[A-Za-z0-9_-]+"
_DRAFT_ID: Final = r"(?!send$)[A-Za-z0-9_-]+"  # a draft id can never be the word "send"
_SEG: Final = r"[^/]+"

GOOGLE_OAUTH: Final = (
    Endpoint("POST", "oauth2.googleapis.com", r"/token"),
    Endpoint("POST", "oauth2.googleapis.com", r"/revoke"),
    Endpoint("GET", "openidconnect.googleapis.com", r"/v1/userinfo"),
)

GMAIL: Final = (
    Endpoint("GET", "gmail.googleapis.com", r"/gmail/v1/users/me/profile"),
    Endpoint("GET", "gmail.googleapis.com", r"/gmail/v1/users/me/labels"),
    Endpoint("GET", "gmail.googleapis.com", r"/gmail/v1/users/me/messages"),
    Endpoint("GET", "gmail.googleapis.com", rf"/gmail/v1/users/me/messages/{_ID}"),
    Endpoint(
        "GET", "gmail.googleapis.com", rf"/gmail/v1/users/me/messages/{_ID}/attachments/{_ID}"
    ),
    Endpoint("GET", "gmail.googleapis.com", r"/gmail/v1/users/me/threads"),
    Endpoint("GET", "gmail.googleapis.com", rf"/gmail/v1/users/me/threads/{_ID}"),
    Endpoint("GET", "gmail.googleapis.com", r"/gmail/v1/users/me/drafts"),
    Endpoint("GET", "gmail.googleapis.com", rf"/gmail/v1/users/me/drafts/{_DRAFT_ID}"),
    Endpoint("POST", "gmail.googleapis.com", r"/gmail/v1/users/me/drafts"),
    Endpoint("PUT", "gmail.googleapis.com", rf"/gmail/v1/users/me/drafts/{_DRAFT_ID}"),
)

GOOGLE_CALENDAR: Final = (
    Endpoint("GET", "www.googleapis.com", r"/calendar/v3/users/me/calendarList"),
    Endpoint("POST", "www.googleapis.com", r"/calendar/v3/freeBusy"),
    Endpoint("GET", "www.googleapis.com", rf"/calendar/v3/calendars/{_SEG}/events"),
    Endpoint("GET", "www.googleapis.com", rf"/calendar/v3/calendars/{_SEG}/events/{_SEG}"),
    # Writes: used only after approval of the exact operation (calendar.write, P3).
    Endpoint("POST", "www.googleapis.com", rf"/calendar/v3/calendars/{_SEG}/events"),
    Endpoint("PATCH", "www.googleapis.com", rf"/calendar/v3/calendars/{_SEG}/events/{_SEG}"),
    Endpoint("DELETE", "www.googleapis.com", rf"/calendar/v3/calendars/{_SEG}/events/{_SEG}"),
)

GOOGLE_DRIVE: Final = (
    Endpoint("GET", "www.googleapis.com", r"/drive/v3/files"),
    Endpoint("GET", "www.googleapis.com", rf"/drive/v3/files/{_SEG}"),
    Endpoint("GET", "www.googleapis.com", rf"/drive/v3/files/{_SEG}/export"),
)

MICROSOFT_OAUTH: Final = (
    Endpoint("POST", "login.microsoftonline.com", rf"/{_SEG}/oauth2/v2\.0/devicecode"),
    Endpoint("POST", "login.microsoftonline.com", rf"/{_SEG}/oauth2/v2\.0/token"),
)

MICROSOFT_GRAPH: Final = (
    Endpoint("GET", "graph.microsoft.com", r"/v1\.0/me"),
    Endpoint("GET", "graph.microsoft.com", r"/v1\.0/me/drive"),
    Endpoint("GET", "graph.microsoft.com", r"/v1\.0/me/drives"),
    Endpoint("GET", "graph.microsoft.com", r"/v1\.0/me/drive/root(:/.*:)?(/children)?"),
    Endpoint("GET", "graph.microsoft.com", r"/v1\.0/sites"),
    Endpoint("GET", "graph.microsoft.com", rf"/v1\.0/sites/{_SEG}(/drives)?"),
    Endpoint("GET", "graph.microsoft.com", rf"/v1\.0/drives/{_SEG}/root(/children)?"),
    Endpoint("GET", "graph.microsoft.com", rf"/v1\.0/drives/{_SEG}/items/{_SEG}"),
    Endpoint("GET", "graph.microsoft.com", rf"/v1\.0/drives/{_SEG}/items/{_SEG}/children"),
    Endpoint("GET", "graph.microsoft.com", rf"/v1\.0/drives/{_SEG}/items/{_SEG}/content"),
)

_ISSUE_KEY: Final = r"[A-Z][A-Z0-9_]+-[0-9]+"


def jira(site_url: str) -> tuple[Endpoint, ...]:
    host = urlsplit(site_url).hostname or ""
    return (
        Endpoint("GET", host, r"/rest/api/3/myself"),
        Endpoint("GET", host, r"/rest/api/3/project/search"),
        Endpoint("GET", host, r"/rest/api/3/search/jql"),
        Endpoint("POST", host, r"/rest/api/3/search/jql"),
        Endpoint("GET", host, rf"/rest/api/3/issue/{_ISSUE_KEY}"),
        Endpoint("GET", host, rf"/rest/api/3/issue/{_ISSUE_KEY}/transitions"),
        Endpoint("GET", host, rf"/rest/api/3/issue/{_ISSUE_KEY}/worklog"),  # read (M10-03)
        Endpoint("GET", host, r"/rest/api/3/issue/createmeta/[A-Z][A-Z0-9_]+/issuetypes"),
        # Writes: only for approved change sets (workitem.write, P3).
        Endpoint("POST", host, r"/rest/api/3/issue"),
        Endpoint("PUT", host, rf"/rest/api/3/issue/{_ISSUE_KEY}"),
        Endpoint("POST", host, rf"/rest/api/3/issue/{_ISSUE_KEY}/transitions"),
        Endpoint("POST", host, rf"/rest/api/3/issue/{_ISSUE_KEY}/comment"),
    )


def azure_devops(org_url: str) -> tuple[Endpoint, ...]:
    parts = urlsplit(org_url)
    host = parts.hostname or ""
    org = re.escape(parts.path.strip("/").split("/")[0]) if parts.path.strip("/") else _SEG
    project = r"[^/]+"
    return (
        Endpoint("GET", host, rf"/{org}/_apis/connectionData"),
        Endpoint("GET", host, rf"/{org}/_apis/projects"),
        Endpoint("POST", host, rf"/{org}/{project}/_apis/wit/wiql"),
        Endpoint("GET", host, rf"/{org}/_apis/wit/workitems"),
        Endpoint("GET", host, rf"/{org}/_apis/wit/workitems/[0-9]+"),
        Endpoint("GET", host, rf"/{org}/{project}/_apis/wit/workitems/[0-9]+/comments"),
        # Writes: only for approved change sets (workitem.write, P3).
        Endpoint("POST", host, rf"/{org}/{project}/_apis/wit/workitems/\$[^/]+"),
        Endpoint("PATCH", host, rf"/{org}/{project}/_apis/wit/workitems/[0-9]+"),
        Endpoint("POST", host, rf"/{org}/{project}/_apis/wit/workitems/[0-9]+/comments"),
    )


def for_account(kind: str, base_url: str | None) -> tuple[Endpoint, ...]:
    if kind == "google":
        return GOOGLE_OAUTH + GMAIL + GOOGLE_CALENDAR + GOOGLE_DRIVE
    if kind == "microsoft":
        return MICROSOFT_OAUTH + MICROSOFT_GRAPH
    if kind == "jira":
        return jira(base_url or "")
    if kind == "azure_devops":
        return azure_devops(base_url or "")
    raise ValueError(f"unknown connector kind {kind!r}")
