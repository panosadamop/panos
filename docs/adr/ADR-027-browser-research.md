# ADR-027: Browser research

- **Status:** Proposed (for acceptance at the M8 exit review)
- **Date:** 2026-10-12
- **Traces:** BR-022, FR-016, US-045, AC-US045-01..03, ADR-023

## Context
FR-016 asks for open/navigate/read research. BR-022 forbids autonomously submitting consequential forms or uploading content. Web pages are adversarial: they can contain hidden instructions, auto-submitting forms and scripts that POST data. The owner chose Playwright Chromium with an isolated profile (2026-10-09).

## Decision
1. **Engine.** Playwright Chromium with a dedicated persistent profile under the Panos data directory, never the user's own browser profile. It is headless by default, refuses downloads and blocks service workers. Playwright is an optional extra (`panos-core[browser]`).
2. **Read-only by construction.**
   - The engine port (`BrowserEngine`) has only `read`, `search` and `close`. There is no click, type, fill or upload operation.
   - A route guard aborts every request that is not GET or HEAD, including requests made by the page's own scripts.
   - An init script neutralises form submission inside the page. Without it, a blocked submit would replace the page with an error page.
   - The registry refuses any tool declaring `browser.submit` or `browser.upload` (they stay P4 in policy, with no tool).
3. **Network boundary.** Only http(s) URLs. Loopback, private, link-local and other non-global addresses are blocked (checked before navigation and per request) unless `browser.allow_private_networks` is set.
4. **Tools.** `browser.read` returns text, title, links and a citation (final URL, title, retrieval time, content hash). `browser.search` sends a GET to a configurable search page (`browser.search_url`, default DuckDuckGo HTML) and unwraps result links. Both are `browser.read` (P0), and both mark their output untrusted.
5. **Local-only projects.** Both tools are refused when the tool context is a LOCAL_ONLY project, because any outbound request (a query, a model-composed URL) could carry protected context off the machine. A chat "research …" request in a local-only project starts the research **outside** the project and says so.
6. **Research workflow.** A `research` job:
   1. searches, unless URLs are given;
   2. prefers one page per site;
   3. reads up to N sources, one step each, with progress kept in the job state;
   4. answers via the AI gateway from those pages only, wrapped as untrusted context with the `external` scope, citing them as `[n]`.

   The report lists the sources with hashes and flags an answer that cites nothing. Pause, resume and cancel work at safe points between steps. The answer routes with the job's privacy (LOCAL_ONLY without a project).

## Alternatives considered
- **HTTP fetch only (no browser):** cheaper, but script-rendered pages come back empty. Fetch is kept for knowledge *collections* of selected websites (robots.txt honoured, size-limited).
- **Driving the user's own browser:** exposes the user's cookies and sessions. Rejected.
- **A submit tool behind P4 approval:** deferred. It needs a reviewed, user-driven flow (what is submitted, where, preview), which is out of M8.

## Consequences
- Chromium must be installed (`python -m playwright install chromium`). The packaged app needs a decision on bundling it (see `considerations.md`).
- Some sites block automated browsers, and search-engine HTML can change. The search page is configurable, and results are parsed generically.
- Research inside local-only projects is not possible by design.
