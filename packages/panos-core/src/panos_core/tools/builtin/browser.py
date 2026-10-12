"""Browser research tools (ADR-027, FR-016, BR-022, US-045).

* ``browser.read``: open one public page (GET) and return its text, title, links and a
  citation. Page content is untrusted data (AC-US045-03).
* ``browser.search``: run a GET search and return result links.

There is no submit, fill, click or upload tool, and the registry refuses to register one
(AC-US045-02). In a LOCAL_ONLY project the tools are refused: any outbound request (a
search query, a URL a model composed) could carry protected context off the machine.
"""

from datetime import datetime
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from panos_core.browser.engine import BrowserEngine, BrowserError, check_url
from panos_core.domain.enums import PrivacyMode
from panos_core.tools.contract import (
    ActionPlan,
    PlanRejectedError,
    SideEffect,
    Tool,
    ToolContext,
    ToolExecutionError,
    ToolSpec,
)

LOCAL_ONLY_REFUSAL: Final = (
    "web access is disabled in local-only projects: a query or URL could carry protected "
    "context off this machine (run the research outside the project)"
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _refuse_local_only(ctx: ToolContext) -> None:
    if ctx.project is not None and ctx.project.privacy_mode == PrivacyMode.LOCAL_ONLY:
        raise PlanRejectedError(LOCAL_ONLY_REFUSAL)


class Citation(_Model):
    url: str
    title: str
    retrieved_at: datetime
    content_hash: str


class LinkOut(_Model):
    text: str
    url: str


class BrowserReadInput(_Model):
    url: str = Field(min_length=8, max_length=2000, pattern=r"^https?://")
    max_chars: int = Field(default=20_000, ge=500, le=100_000)


class BrowserReadOutput(_Model):
    url: str
    final_url: str
    title: str
    text: str
    truncated: bool
    links: list[LinkOut]
    citation: Citation
    untrusted: bool = True
    privacy_mode: str = PrivacyMode.STANDARD.value  # public web content


class BrowserReadTool(Tool[BrowserReadInput, BrowserReadOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="browser.read",
        version="1",
        description="Open a public web page (read-only) and return its text, links and citation.",
        input_model=BrowserReadInput,
        output_model=BrowserReadOutput,
        capabilities=frozenset({"browser.read"}),
        side_effect=SideEffect.NONE,
        timeout_s=90,
        cancellable=False,
        idempotent=True,
        privacy="Sends a GET request to the page's site; refused in local-only projects.",
    )

    def __init__(self, engine: BrowserEngine, *, allow_private: bool = False) -> None:
        self._engine = engine
        self._allow_private = allow_private

    async def plan(self, inp: BrowserReadInput, ctx: ToolContext) -> ActionPlan:
        _refuse_local_only(ctx)
        try:
            await check_url(inp.url, allow_private=self._allow_private)
        except BrowserError as exc:
            raise PlanRejectedError(str(exc)) from exc
        return ActionPlan(
            action="browser.read",
            target=inp.url[:500],
            summary=f"Read {inp.url[:200]}",
            payload={"url": inp.url},
        )

    async def _execute(
        self, inp: BrowserReadInput, ctx: ToolContext, plan: ActionPlan
    ) -> BrowserReadOutput:
        try:
            page = await self._engine.read(inp.url)
        except BrowserError as exc:
            raise ToolExecutionError(str(exc)) from exc
        return BrowserReadOutput(
            url=page.url,
            final_url=page.final_url,
            title=page.title,
            text=page.text[: inp.max_chars],
            truncated=len(page.text) > inp.max_chars,
            links=[LinkOut(text=t, url=u) for t, u in page.links[:50]],
            citation=Citation(
                url=page.final_url,
                title=page.title,
                retrieved_at=page.retrieved_at,
                content_hash=page.content_hash,
            ),
        )


class BrowserSearchInput(_Model):
    query: str = Field(min_length=2, max_length=300)
    limit: int = Field(default=8, ge=1, le=20)


class SearchResultOut(_Model):
    title: str
    url: str


class BrowserSearchOutput(_Model):
    query: str
    results: list[SearchResultOut]
    untrusted: bool = True


class BrowserSearchTool(Tool[BrowserSearchInput, BrowserSearchOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="browser.search",
        version="1",
        description="Search the web (GET only) and return result titles and links.",
        input_model=BrowserSearchInput,
        output_model=BrowserSearchOutput,
        capabilities=frozenset({"browser.read"}),
        side_effect=SideEffect.NONE,
        timeout_s=90,
        cancellable=False,
        idempotent=True,
        privacy="Sends the query to the configured search site; refused in local-only projects.",
    )

    def __init__(self, engine: BrowserEngine) -> None:
        self._engine = engine

    async def plan(self, inp: BrowserSearchInput, ctx: ToolContext) -> ActionPlan:
        _refuse_local_only(ctx)
        return ActionPlan(
            action="browser.read",
            target="web-search",
            summary=f"Search the web for {inp.query[:120]!r}",
            payload={"query": inp.query},
        )

    async def _execute(
        self, inp: BrowserSearchInput, ctx: ToolContext, plan: ActionPlan
    ) -> BrowserSearchOutput:
        try:
            results = await self._engine.search(inp.query, limit=inp.limit)
        except BrowserError as exc:
            raise ToolExecutionError(str(exc)) from exc
        return BrowserSearchOutput(
            query=inp.query,
            results=[SearchResultOut(title=r.title, url=r.url) for r in results],
        )
