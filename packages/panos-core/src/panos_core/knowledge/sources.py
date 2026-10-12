"""Knowledge collection sources (FR-019, M8-04).

Each source gathers :class:`Document` objects for one collection, incrementally: a document
whose version is already indexed (same ``content_hash``) is only marked as *seen*. The URI
is the provenance a person can follow (file path or web link).

* **folder**: a local company folder, walked like a project (ignores, size limits).
* **website**: only the listed pages, no crawling. ``robots.txt`` is honoured, redirects
  stay on the same host, and pages are size-limited. Content is untrusted data.
* **Google Drive / OneDrive / SharePoint**: a folder through a connector account,
  read-only and allowlisted (ADR-026). Remote versions (modified time + checksum) decide
  what is downloaded again.
"""

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from panos_core import __version__
from panos_core.connectors.http import ConnectorHttp, ConnectorHttpError
from panos_core.knowledge.extract import MAX_DOCUMENT_BYTES, extractable
from panos_core.knowledge.index import MAX_FILE_BYTES, Document, document_from_bytes
from panos_core.observability import get_logger
from panos_core.repository.walk import iter_project_files

log = get_logger(__name__)

USER_AGENT: Final = f"PanosKnowledge/{__version__} (+local desktop assistant)"
MAX_PAGE_BYTES: Final = 5_000_000
MAX_REDIRECTS: Final = 5
MAX_URLS: Final = 200
MAX_REMOTE_FILES: Final = 2_000
MAX_DEPTH: Final = 8
REMOTE_ID_RE: Final = re.compile(r"^[A-Za-z0-9_!.-]{1,200}$")


@dataclass
class Gathered:
    changed: list[Document] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    skipped: list[str] = field(default_factory=list)  # uri: reason (too large, blocked …)

    def add(self, doc: Document, known: dict[str, str]) -> None:
        self.seen.add(doc.uri)
        if known.get(doc.uri) != doc.content_hash:
            self.changed.append(doc)


def _limit(name: str) -> int:
    return MAX_DOCUMENT_BYTES if extractable(name) else MAX_FILE_BYTES


# ====================================================================== folder
def collect_folder(root: Path, known: dict[str, str]) -> Gathered:
    out = Gathered()
    for f in iter_project_files(root):
        if f.size > _limit(f.rel):
            out.skipped.append(f"{f.rel}: too large")
            continue
        try:
            data = f.path.read_bytes()
        except OSError:
            continue
        if known.get(f.rel) == hashlib.sha256(data).hexdigest():
            out.seen.add(f.rel)
            continue
        doc = document_from_bytes(f.rel, data)
        if doc is not None:
            out.add(doc, known)
    return out


# ====================================================================== website
class _TextExtractor(HTMLParser):
    SKIP: Final = frozenset({"script", "style", "noscript", "template", "svg", "head", "iframe"})
    BLOCK: Final = frozenset(
        {"p", "div", "br", "li", "tr", "section", "article", "header", "footer", "pre", "table"}
        | {"h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "dd", "dt"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """(title, readable text) without scripts, styles or markup."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    lines = (re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in "".join(parser.parts).split("\n"))
    text = "\n".join(ln for ln in lines if ln)
    return parser.title.strip(), text


def _http_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.hostname)


class WebsiteSource:
    """Fetches selected pages (GET only, no forms, no scripts executed)."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._robots: dict[str, RobotFileParser | bool] = {}

    async def _allowed(self, client: httpx.AsyncClient, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rules: RobotFileParser | bool
            r = await client.get(f"{origin}/robots.txt")  # unreachable: raised, not cached
            if r.status_code < 300:
                rules = RobotFileParser()
                rules.parse(r.text[:500_000].splitlines())
            else:
                # No robots.txt (4xx) allows everything; 401/403 and server errors
                # (unknown rules) are treated as "keep out".
                rules = r.status_code < 500 and r.status_code not in (401, 403)
            self._robots[origin] = rules
        rules = self._robots[origin]
        return rules if isinstance(rules, bool) else rules.can_fetch(USER_AGENT, url)

    async def fetch(self, client: httpx.AsyncClient, url: str) -> tuple[str, bytes, str] | str:
        """(final url, body, content type), or the reason the page was skipped."""
        host = urlsplit(url).hostname
        for _ in range(MAX_REDIRECTS + 1):
            if not await self._allowed(client, url):
                return "disallowed by robots.txt"
            async with client.stream("GET", url) as r:
                if r.is_redirect:
                    nxt = urljoin(url, r.headers.get("Location", ""))
                    if not _http_url(nxt) or urlsplit(nxt).hostname != host:
                        return "redirects to another site"
                    url = nxt
                    continue
                if r.status_code >= 400:
                    return f"HTTP {r.status_code}"
                declared = r.headers.get("Content-Length", "")
                if declared.isdigit() and int(declared) > MAX_PAGE_BYTES:
                    return "too large"
                data = bytearray()
                async for part in r.aiter_bytes():
                    data += part
                    if len(data) > MAX_PAGE_BYTES:
                        return "too large"
                return url, bytes(data), r.headers.get("content-type", "")
        return "too many redirects"

    async def collect(self, urls: Iterable[str], known: dict[str, str]) -> Gathered:
        out = Gathered()
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=30.0,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,application/pdf"},
        ) as client:
            for url in list(urls)[:MAX_URLS]:
                try:
                    result = await self.fetch(client, url)
                except httpx.HTTPError as exc:
                    result = f"failed ({type(exc).__name__})"
                if isinstance(result, str):
                    out.skipped.append(f"{url}: {result}")
                    if result.startswith(("failed", "HTTP 5")):
                        out.seen.add(url)  # transient: keep the last indexed version
                    continue
                final, data, ctype = result
                doc = self._document(url, final, data, ctype)
                if doc is None:
                    out.skipped.append(f"{url}: unsupported content")
                    continue
                out.add(doc, known)
        return out

    @staticmethod
    def _document(url: str, final: str, data: bytes, ctype: str) -> Document | None:
        digest = hashlib.sha256(data).hexdigest()
        if "pdf" in ctype:
            doc = document_from_bytes("page.pdf", data)
            return replace(doc, uri=url) if doc else None
        if "html" in ctype or "xml" in ctype:
            charset = re.search(r"charset=([\w-]+)", ctype)
            try:
                html = data.decode(charset.group(1) if charset else "utf-8", errors="replace")
            except LookupError:
                html = data.decode("utf-8", errors="replace")
            title, text = html_to_text(html)
            header = f"# {title}\nSource: {final}\n\n" if title else f"Source: {final}\n\n"
            return Document(uri=url, text=header + text, content_hash=digest, size=len(data))
        if ctype.startswith("text/"):
            text = data.decode("utf-8", errors="replace")
            return Document(uri=url, text=text, content_hash=digest, size=len(data))
        return None


# ====================================================================== cloud drives
GOOGLE_FOLDER: Final = "application/vnd.google-apps.folder"
GOOGLE_EXPORTS: Final = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}
DRIVE: Final = "https://www.googleapis.com/drive/v3/files"
GRAPH: Final = "https://graph.microsoft.com/v1.0"


def remote_id(value: str, what: str) -> str:
    if not REMOTE_ID_RE.match(value):
        raise ValueError(f"invalid {what} {value!r}")
    return value


def _version(*parts: object) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()


def _from_bytes(name: str, uri: str, data: bytes, version: str) -> Document | None:
    doc = document_from_bytes(name, data)
    return replace(doc, uri=uri, content_hash=version) if doc else None


async def collect_google_drive(
    http: ConnectorHttp, folder_id: str, known: dict[str, str]
) -> Gathered:
    out = Gathered()
    queue: list[tuple[str, str, int]] = [(remote_id(folder_id, "folder id"), "", 0)]
    files = 0
    while queue:
        folder, prefix, depth = queue.pop(0)
        token: str | None = None
        while True:
            params: dict[str, Any] = {
                "q": f"'{folder}' in parents and trashed = false",
                "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size,md5Checksum,"
                "webViewLink)",
                "pageSize": 200,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if token:
                params["pageToken"] = token
            data = await http.request("GET", DRIVE, params=params) or {}
            for f in data.get("files", []):
                name = f"{prefix}{f.get('name', f['id'])}"
                if f.get("mimeType") == GOOGLE_FOLDER:
                    if depth < MAX_DEPTH:
                        queue.append((remote_id(f["id"], "folder id"), f"{name}/", depth + 1))
                    continue
                files += 1
                if files > MAX_REMOTE_FILES:
                    out.skipped.append(f"{name}: more than {MAX_REMOTE_FILES} files")
                    return out
                await _drive_file(http, f, name, known, out)
            token = data.get("nextPageToken")
            if not token:
                break
    return out


async def _drive_file(
    http: ConnectorHttp, f: dict[str, Any], name: str, known: dict[str, str], out: Gathered
) -> None:
    uri = f.get("webViewLink") or f"https://drive.google.com/file/d/{f['id']}/view"
    version = _version(f["id"], f.get("modifiedTime"), f.get("md5Checksum") or f.get("size"))
    if known.get(uri) == version:
        out.seen.add(uri)
        return
    file_id = quote(f["id"])
    export = GOOGLE_EXPORTS.get(f.get("mimeType", ""))
    try:
        if export is not None:
            data = await http.download(
                f"{DRIVE}/{file_id}/export",
                params={"mimeType": export[0]},
                max_bytes=MAX_FILE_BYTES,
            )
            name += export[1]
        elif str(f.get("mimeType", "")).startswith("application/vnd.google-apps"):
            return  # forms, drawings, shortcuts: no text
        elif int(f.get("size") or 0) > _limit(name):
            out.skipped.append(f"{name}: too large")
            return
        else:
            data = await http.download(
                f"{DRIVE}/{file_id}", params={"alt": "media"}, max_bytes=_limit(name)
            )
    except ConnectorHttpError as exc:
        out.skipped.append(f"{name}: {exc}")
        out.seen.add(uri)  # keep the last indexed version
        return
    doc = _from_bytes(name, uri, data, version) if data is not None else None
    if doc is None:
        out.skipped.append(f"{name}: unsupported or too large")
        return
    out.add(doc, known)


async def collect_onedrive(
    http: ConnectorHttp, drive_id: str, item_id: str, known: dict[str, str]
) -> Gathered:
    """A OneDrive or SharePoint document-library folder (a Graph drive item)."""
    out = Gathered()
    drive = quote(remote_id(drive_id, "drive id"))
    queue: list[tuple[str, str, int]] = [(remote_id(item_id or "root", "item id"), "", 0)]
    files = 0
    while queue:
        item, prefix, depth = queue.pop(0)
        url: str | None = f"{GRAPH}/drives/{drive}/items/{quote(item)}/children"
        params: dict[str, Any] | None = {
            "$select": "id,name,file,folder,size,webUrl,lastModifiedDateTime,eTag",
            "$top": 200,
        }
        while url:
            data = await http.request("GET", url, params=params) or {}
            for entry in data.get("value", []):
                name = f"{prefix}{entry.get('name', entry['id'])}"
                if "folder" in entry:
                    if depth < MAX_DEPTH:
                        queue.append((remote_id(entry["id"], "item id"), f"{name}/", depth + 1))
                    continue
                if "file" not in entry:
                    continue
                files += 1
                if files > MAX_REMOTE_FILES:
                    out.skipped.append(f"{name}: more than {MAX_REMOTE_FILES} files")
                    return out
                await _graph_file(http, drive, entry, name, known, out)
            url, params = data.get("@odata.nextLink"), None
    return out


async def _graph_file(
    http: ConnectorHttp,
    drive: str,
    entry: dict[str, Any],
    name: str,
    known: dict[str, str],
    out: Gathered,
) -> None:
    uri = entry.get("webUrl") or f"onedrive:{drive}/{entry['id']}"
    version = _version(entry["id"], entry.get("eTag"), entry.get("lastModifiedDateTime"))
    if known.get(uri) == version:
        out.seen.add(uri)
        return
    if int(entry.get("size") or 0) > _limit(name):
        out.skipped.append(f"{name}: too large")
        return
    try:
        data = await http.download(
            f"{GRAPH}/drives/{drive}/items/{quote(entry['id'])}/content", max_bytes=_limit(name)
        )
    except ConnectorHttpError as exc:
        out.skipped.append(f"{name}: {exc}")
        out.seen.add(uri)
        return
    doc = _from_bytes(name, uri, data, version) if data is not None else None
    if doc is None:
        out.skipped.append(f"{name}: unsupported or too large")
        return
    out.add(doc, known)
