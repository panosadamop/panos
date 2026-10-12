"""Browser engine port and URL guard (ADR-027).

The engine can only *read*: it navigates with GET, returns the page's visible text, title
and links, and never fills, clicks or submits anything. There is deliberately no method for
forms, clicks, uploads or downloads (AC-US045-02); the Playwright adapter additionally
aborts every non-GET request the page itself tries to make.
"""

import asyncio
import base64
import hashlib
import ipaddress
import socket
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Protocol
from urllib.parse import parse_qs, urlsplit

from panos_core.domain.clock import utc_now

MAX_TEXT_CHARS: Final = 200_000
MAX_LINKS: Final = 100


class BrowserError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason  # blocked | unavailable | failed | timeout


@dataclass(frozen=True)
class PageSnapshot:
    url: str  # what was requested
    final_url: str  # after redirects
    title: str
    text: str
    links: list[tuple[str, str]]  # (text, absolute URL), http(s) only
    status: int
    retrieved_at: datetime = field(default_factory=utc_now)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str


class BrowserEngine(Protocol):
    async def read(self, url: str) -> PageSnapshot: ...
    async def search(self, query: str, *, limit: int) -> list[SearchResult]: ...
    async def close(self) -> None: ...


def _private(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    return not ip.is_global


async def check_url(url: str, *, allow_private: bool = False) -> None:
    """Raise :class:`BrowserError` unless ``url`` is a public http(s) address."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BrowserError("blocked", f"only http(s) pages can be opened, not {url[:100]!r}")
    if allow_private:
        return
    host = parts.hostname
    try:
        addresses = [host] if _is_ip(host) else await _resolve(host)
    except OSError as exc:
        raise BrowserError("failed", f"cannot resolve {host}") from exc
    if any(_private(a) for a in addresses):
        raise BrowserError(
            "blocked",
            f"{host} is a local or private-network address "
            "(set browser.allow_private_networks to allow it)",
        )


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


async def _resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def unwrap_redirect(url: str) -> str:
    """Search engines wrap result links; return the target when the wrapper carries it.

    DuckDuckGo: ``/l/?uddg=<url>``; Bing: ``/ck/a?…&u=a1<base64url(url)>``; others: ``url=``,
    ``u=`` or ``q=`` with a plain URL.
    """
    query = parse_qs(urlsplit(url).query)
    for key in ("uddg", "url", "u", "q"):
        for value in query.get(key, []):
            if value.startswith(("http://", "https://")):
                return value
            if key == "u" and value.startswith("a1"):
                try:
                    raw = value[2:]
                    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
                except (ValueError, UnicodeDecodeError):
                    continue
                if decoded.startswith(("http://", "https://")):
                    return decoded
    return url


def _site(host: str) -> str:
    """``html.duckduckgo.com`` / ``www.bing.com`` → ``duckduckgo.com`` / ``bing.com``."""
    parts = host.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def search_results(links: list[tuple[str, str]], search_url: str, limit: int) -> list[SearchResult]:
    """Result links of a search page: unwrapped, external, de-duplicated."""
    search_host = _site(urlsplit(search_url).hostname or "")
    seen: set[str] = set()
    out: list[SearchResult] = []
    for text, href in links:
        target = unwrap_redirect(href)
        host = urlsplit(target).hostname or ""
        if not host or _site(host) == search_host or target in seen:
            continue
        seen.add(target)
        out.append(SearchResult(title=(text.strip() or host)[:200], url=target))
        if len(out) >= limit:
            break
    return out
