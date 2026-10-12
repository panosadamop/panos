"""Jira Cloud and Azure DevOps work items: read, prepare and apply changes (BR-016, M8-06).

Reading is free (P0). A change set is *prepared* into the exact HTTP requests that will be
sent; that list is what the user approves (P3, AC-US042-02), and :meth:`apply` sends only
those requests. There is no delete operation.
"""

from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol
from urllib.parse import quote

from panos_core.connectors.http import ConnectorHttp

ADO_API: Final = "7.1"
ADO_COMMENTS_API: Final = "7.1-preview.4"
MAX_DESCRIPTION: Final = 4000

ChangeKind = Literal["create", "update", "transition", "comment"]


@dataclass(frozen=True)
class WorkItem:
    key: str  # SHOP-12 / 4711
    title: str
    status: str
    type: str
    assignee: str
    priority: str
    updated: str
    url: str
    description: str = ""


@dataclass(frozen=True)
class Change:
    kind: ChangeKind
    key: str | None = None  # existing item (not for create)
    project: str | None = None  # create: Jira project key / ADO project name
    type: str | None = None  # create: issue / work item type
    title: str | None = None
    description: str | None = None
    status: str | None = None  # transition target (name)
    comment: str | None = None
    fields: dict[str, Any] | None = None  # update: extra raw fields (validated by the service)


@dataclass(frozen=True)
class PreparedRequest:
    method: str
    url: str
    body: Any
    content_type: str = "application/json"
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "url": self.url,
            "body": self.body,
            "content_type": self.content_type,
            "summary": self.summary,
        }


class WorkItemClient(Protocol):
    system: str

    async def search(self, query: str, *, limit: int = 20) -> list[WorkItem]: ...
    async def get(self, key: str) -> WorkItem: ...
    async def prepare(self, change: Change) -> PreparedRequest: ...


async def apply(http: ConnectorHttp, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Send prepared requests in order; stop at the first failure (raised to the caller)."""
    results = []
    for req in requests:
        data = await http.request(
            req["method"],
            req["url"],
            json=req["body"],
            headers={"Content-Type": req["content_type"]},
        )
        results.append(data if isinstance(data, dict) else {})
    return results


# ---------------------------------------------------------------------- Jira Cloud
def adf(text: str) -> dict[str, Any]:
    """Plain text → Atlassian Document Format (one paragraph per line block)."""
    paragraphs = [p for p in text.split("\n\n") if p.strip()] or [""]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p}] if p else []}
            for p in paragraphs
        ],
    }


def adf_text(node: Any) -> str:
    if isinstance(node, dict):
        if node.get("type") == "text":
            return str(node.get("text", ""))
        inner = "".join(adf_text(c) for c in node.get("content", []))
        return inner + ("\n" if node.get("type") in ("paragraph", "heading", "listItem") else "")
    return ""


class JiraClient:
    system = "jira"
    FIELDS: Final = [
        "summary",
        "status",
        "issuetype",
        "assignee",
        "priority",
        "updated",
        "description",
    ]

    def __init__(self, http: ConnectorHttp, site: str) -> None:
        self._http = http
        self._site = site.rstrip("/")

    def _item(self, data: dict[str, Any]) -> WorkItem:
        f = data.get("fields", {})
        return WorkItem(
            key=data["key"],
            title=f.get("summary", ""),
            status=(f.get("status") or {}).get("name", ""),
            type=(f.get("issuetype") or {}).get("name", ""),
            assignee=(f.get("assignee") or {}).get("displayName", ""),
            priority=(f.get("priority") or {}).get("name", ""),
            updated=f.get("updated", ""),
            url=f"{self._site}/browse/{data['key']}",
            description=adf_text(f.get("description"))[:MAX_DESCRIPTION].strip(),
        )

    async def search(self, query: str, *, limit: int = 20) -> list[WorkItem]:
        jql = (
            query
            if any(op in query for op in ("=", "~", " in ", "ORDER BY"))
            else (f'text ~ "{query.replace(chr(34), "")}" ORDER BY updated DESC')
        )
        data = await self._http.request(
            "POST",
            f"{self._site}/rest/api/3/search/jql",
            json={"jql": jql, "maxResults": min(limit, 100), "fields": self.FIELDS},
        )
        return [self._item(i) for i in (data or {}).get("issues", [])]

    async def get(self, key: str) -> WorkItem:
        data = await self._http.request(
            "GET",
            f"{self._site}/rest/api/3/issue/{quote(key)}",
            params={"fields": ",".join(self.FIELDS)},
        )
        return self._item(data)

    async def prepare(self, change: Change) -> PreparedRequest:
        base = f"{self._site}/rest/api/3/issue"
        if change.kind == "create":
            fields: dict[str, Any] = {
                "project": {"key": change.project},
                "issuetype": {"name": change.type or "Task"},
                "summary": change.title,
            }
            if change.description:
                fields["description"] = adf(change.description)
            return PreparedRequest(
                "POST",
                base,
                {"fields": fields},
                summary=f"Create {change.type or 'Task'} in {change.project}: {change.title}",
            )
        key = quote(change.key or "")
        if change.kind == "update":
            fields = dict(change.fields or {})
            if change.title is not None:
                fields["summary"] = change.title
            if change.description is not None:
                fields["description"] = adf(change.description)
            return PreparedRequest(
                "PUT",
                f"{base}/{key}",
                {"fields": fields},
                summary=f"Update {change.key}: {', '.join(sorted(fields))}",
            )
        if change.kind == "comment":
            return PreparedRequest(
                "POST",
                f"{base}/{key}/comment",
                {"body": adf(change.comment or "")},
                summary=f"Comment on {change.key}",
            )
        # transition: resolve the transition id for the target status now, so the approved
        # request is exact.
        data = await self._http.request("GET", f"{base}/{key}/transitions")
        target = (change.status or "").casefold()
        match = next(
            (
                t
                for t in (data or {}).get("transitions", [])
                if t.get("name", "").casefold() == target
                or (t.get("to") or {}).get("name", "").casefold() == target
            ),
            None,
        )
        if match is None:
            names = ", ".join(t.get("name", "") for t in (data or {}).get("transitions", []))
            raise ValueError(f"{change.key} cannot move to {change.status!r} (available: {names})")
        return PreparedRequest(
            "POST",
            f"{base}/{key}/transitions",
            {"transition": {"id": match["id"]}},
            summary=f"Move {change.key} to {match.get('to', {}).get('name') or match.get('name')}",
        )


# ---------------------------------------------------------------------- Azure DevOps
class AzureDevOpsClient:
    system = "azure_devops"
    FIELDS: Final = [
        "System.Title",
        "System.State",
        "System.WorkItemType",
        "System.AssignedTo",
        "Microsoft.VSTS.Common.Priority",
        "System.ChangedDate",
        "System.Description",
        "System.TeamProject",
    ]

    def __init__(self, http: ConnectorHttp, org_url: str, project: str | None = None) -> None:
        self._http = http
        self._org = org_url.rstrip("/")
        self._project = project

    def _item(self, data: dict[str, Any]) -> WorkItem:
        f = data.get("fields", {})
        assignee = f.get("System.AssignedTo")
        project = f.get("System.TeamProject", self._project or "")
        return WorkItem(
            key=str(data["id"]),
            title=f.get("System.Title", ""),
            status=f.get("System.State", ""),
            type=f.get("System.WorkItemType", ""),
            assignee=assignee.get("displayName", "")
            if isinstance(assignee, dict)
            else str(assignee or ""),
            priority=str(f.get("Microsoft.VSTS.Common.Priority", "")),
            updated=f.get("System.ChangedDate", ""),
            url=f"{self._org}/{quote(project)}/_workitems/edit/{data['id']}",
            description=str(f.get("System.Description", ""))[:MAX_DESCRIPTION],
        )

    def _need_project(self, project: str | None) -> str:
        name = project or self._project
        if not name:
            raise ValueError("Azure DevOps needs a project name (--project)")
        return quote(name)

    async def search(self, query: str, *, limit: int = 20) -> list[WorkItem]:
        project = self._need_project(None)
        wiql = (
            query
            if query.lstrip().upper().startswith("SELECT")
            # WIQL is a read-only query language; quotes are escaped by doubling.
            else (
                "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project "  # noqa: S608
                f"AND [System.Title] CONTAINS '{query.replace(chr(39), chr(39) * 2)}' "
                "ORDER BY [System.ChangedDate] DESC"
            )
        )
        data = await self._http.request(
            "POST",
            f"{self._org}/{project}/_apis/wit/wiql",
            params={"api-version": ADO_API, "$top": min(limit, 200)},
            json={"query": wiql},
        )
        ids = [str(w["id"]) for w in (data or {}).get("workItems", [])][:limit]
        if not ids:
            return []
        items = await self._http.request(
            "GET",
            f"{self._org}/_apis/wit/workitems",
            params={"ids": ",".join(ids), "fields": ",".join(self.FIELDS), "api-version": ADO_API},
        )
        return [self._item(i) for i in (items or {}).get("value", [])]

    async def get(self, key: str) -> WorkItem:
        data = await self._http.request(
            "GET",
            f"{self._org}/_apis/wit/workitems/{int(key)}",
            params={"fields": ",".join(self.FIELDS), "api-version": ADO_API},
        )
        return self._item(data)

    async def prepare(self, change: Change) -> PreparedRequest:
        patch = "application/json-patch+json"
        if change.kind == "create":
            project = self._need_project(change.project)
            ops = [{"op": "add", "path": "/fields/System.Title", "value": change.title}]
            if change.description:
                ops.append(
                    {"op": "add", "path": "/fields/System.Description", "value": change.description}
                )
            wit = quote(change.type or "Task")
            return PreparedRequest(
                "POST",
                f"{self._org}/{project}/_apis/wit/workitems/${wit}?api-version={ADO_API}",
                ops,
                patch,
                f"Create {change.type or 'Task'} in {change.project or self._project}: "
                f"{change.title}",
            )
        item_id = int(change.key or "0")
        project = self._need_project(change.project)
        if change.kind == "comment":
            return PreparedRequest(
                "POST",
                f"{self._org}/{project}/_apis/wit/workitems/{item_id}/comments?api-version={ADO_COMMENTS_API}",
                {"text": change.comment or ""},
                summary=f"Comment on #{item_id}",
            )
        ops = []
        if change.kind == "transition":
            ops.append({"op": "add", "path": "/fields/System.State", "value": change.status})
        else:
            if change.title is not None:
                ops.append({"op": "add", "path": "/fields/System.Title", "value": change.title})
            if change.description is not None:
                ops.append(
                    {"op": "add", "path": "/fields/System.Description", "value": change.description}
                )
            for name, value in (change.fields or {}).items():
                ops.append({"op": "add", "path": f"/fields/{name}", "value": value})
        what = (
            f"to {change.status}"
            if change.kind == "transition"
            else ", ".join(str(o["path"]).removeprefix("/fields/") for o in ops)
        )
        return PreparedRequest(
            "PATCH",
            f"{self._org}/{project}/_apis/wit/workitems/{item_id}?api-version={ADO_API}",
            ops,
            patch,
            f"{'Move' if change.kind == 'transition' else 'Update'} #{item_id} {what}",
        )
