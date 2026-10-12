"""Browser research (ADR-027, FR-016, US-045): read-only pages, citations, no submissions."""

from panos_core.browser.engine import (
    BrowserEngine,
    BrowserError,
    PageSnapshot,
    SearchResult,
    check_url,
)

__all__ = ["BrowserEngine", "BrowserError", "PageSnapshot", "SearchResult", "check_url"]
