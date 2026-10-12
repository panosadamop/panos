"""Playwright Chromium adapter (ADR-027).

* A dedicated, persistent profile under the Panos data directory, never the user's own
  browser profile: no cookies, passwords or sessions of the user are reachable.
* Every request the page makes goes through a route handler that aborts anything except
  GET to public http(s) addresses, so scripts on a page cannot POST, and forms cannot
  submit, even by accident. Downloads are refused; service workers are blocked.
* Pages are read and closed; the engine exposes no click, type, fill or upload operation.
"""

import asyncio
import contextlib
import os
import sys
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote_plus, urlsplit

from panos_core.browser.engine import (
    MAX_LINKS,
    MAX_TEXT_CHARS,
    BrowserError,
    PageSnapshot,
    SearchResult,
    check_url,
    search_results,
)
from panos_core.config.settings import BrowserSettings
from panos_core.observability import get_logger

log = get_logger(__name__)

_EXTRACT: Final = """() => ({
  title: document.title || '',
  text: document.body ? document.body.innerText : '',
  links: Array.from(document.querySelectorAll('a[href]')).slice(0, 1000)
    .map(a => [(a.innerText || a.getAttribute('aria-label') || a.title || '').trim().slice(0, 200),
                a.href]),
})"""


# Runs before any page script: forms cannot be submitted, by the user's click or by script.
# The route guard below still aborts any non-GET request (defence in depth); this keeps a
# blocked submission from replacing the page being read with an error page.
_NO_SUBMIT: Final = """(() => {
  const noop = function () {};
  HTMLFormElement.prototype.submit = noop;
  HTMLFormElement.prototype.requestSubmit = noop;
  window.addEventListener('submit', e => { e.preventDefault(); e.stopImmediatePropagation(); },
                          true);
})();"""


def _browsers_path() -> Path | None:
    """Where ``playwright install`` puts browsers. A frozen build would otherwise look inside
    its own bundle, so it is pointed at the standard per-user cache instead."""
    if not getattr(sys, "frozen", False) or os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return None
    platform = str(sys.platform)  # a plain str, so mypy keeps all three branches
    if platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / "ms-playwright"
    if platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


class PlaywrightEngine:
    def __init__(self, profile_dir: Path, settings: BrowserSettings) -> None:
        self._profile_dir = profile_dir
        self._settings = settings
        self._lock = asyncio.Lock()
        self._pw: Any = None
        self._context: Any = None
        self._host_ok: dict[str, bool] = {}

    async def _ensure(self) -> Any:
        async with self._lock:
            if self._context is not None:
                return self._context
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise BrowserError(
                    "unavailable",
                    "browser research needs Playwright: install panos-core[browser] and run "
                    "`python -m playwright install chromium`",
                ) from exc
            self._profile_dir.mkdir(parents=True, exist_ok=True)
            browsers = _browsers_path()
            if browsers is not None:
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
            self._pw = await async_playwright().start()
            try:
                self._context = await self._pw.chromium.launch_persistent_context(
                    str(self._profile_dir),
                    headless=self._settings.headless,
                    accept_downloads=False,
                    service_workers="block",
                    java_script_enabled=True,
                    locale="en-US",
                )
            except Exception as exc:
                await self._pw.stop()
                self._pw = None
                first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
                raise BrowserError(
                    "unavailable",
                    f"Chromium could not start ({first[:300]}); run "
                    "`python -m playwright install chromium`",
                ) from exc
            await self._context.add_init_script(_NO_SUBMIT)
            await self._context.route("**/*", self._guard)
            log.info("browser_started", profile=str(self._profile_dir))
            return self._context

    async def _guard(self, route: Any) -> None:
        request = route.request
        if request.method not in ("GET", "HEAD"):
            log.info("browser_request_blocked", method=request.method)
            await route.abort("blockedbyclient")
            return
        url = request.url
        if url.startswith(("data:", "blob:", "about:")):
            await route.continue_()
            return
        host = urlsplit(url).hostname or ""
        if host not in self._host_ok:
            try:
                await check_url(url, allow_private=self._settings.allow_private_networks)
                self._host_ok[host] = True
            except BrowserError:
                self._host_ok[host] = False
        if self._host_ok[host]:
            await route.continue_()
        else:
            await route.abort("blockedbyclient")

    async def read(self, url: str) -> PageSnapshot:
        await check_url(url, allow_private=self._settings.allow_private_networks)
        context = await self._ensure()
        page = await context.new_page()
        try:
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=self._settings.page_timeout_s * 1000
            )
            # Give script-rendered pages a moment, without waiting forever.
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=3000)
            data = await page.evaluate(_EXTRACT)
            final_url = page.url
        except BrowserError:
            raise
        except Exception as exc:
            name = type(exc).__name__
            reason = "timeout" if "Timeout" in name else "failed"
            raise BrowserError(reason, f"could not read {url[:200]}: {name}") from exc
        finally:
            await page.close()
        links: list[tuple[str, str]] = []
        seen: set[str] = set()
        for text, href in data.get("links", []):
            if href.startswith(("http://", "https://")) and href not in seen:
                seen.add(href)
                links.append((str(text), str(href)))
        return PageSnapshot(
            url=url,
            final_url=final_url,
            title=str(data.get("title", ""))[:500],
            text=str(data.get("text", ""))[:MAX_TEXT_CHARS],
            links=links[: MAX_LINKS * 5],
            status=response.status if response is not None else 0,
        )

    async def search(self, query: str, *, limit: int) -> list[SearchResult]:
        url = self._settings.search_url.replace("{query}", quote_plus(query))
        page = await self.read(url)
        results = search_results(page.links, url, limit)
        if not results:
            # Typically a bot check ("confirm you are human"): say so instead of "no results".
            raise BrowserError(
                "blocked",
                f"the search page {urlsplit(url).hostname} returned no result links (it may "
                "be asking for a bot check); set browser.search_url or give the URLs to read",
            )
        return results

    async def close(self) -> None:
        async with self._lock:
            if self._context is not None:
                await self._context.close()
                self._context = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None
