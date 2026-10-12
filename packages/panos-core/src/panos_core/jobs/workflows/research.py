"""Research job (ADR-027, FR-016, US-045, AC-US045-01): search, read sources, cite.

1. Search the web (``browser.search``) unless the user gave the URLs.
2. Read up to ``max_sources`` pages (``browser.read``), one step each; what was read is
   kept in the job state, so a paused or interrupted job resumes without re-reading.
3. Answer with the AI gateway from those pages only, citing them as ``[n]``. Pages are
   wrapped as untrusted data (ADR-023): instructions in them are not followed.

Cancellation and pause take effect between steps (safe points). The answer routes with the
job's privacy (LOCAL_ONLY without a project); the browser tools refuse LOCAL_ONLY projects.
"""

import re
from typing import Any, Final
from urllib.parse import urlsplit

from panos_core.ai.context import RetrievedItem, build_messages
from panos_core.ai.gateway import CallContext
from panos_core.ai.types import ChatRequest, Complexity, TaskType
from panos_core.domain.enums import PrivacyMode
from panos_core.jobs.context import Blocked, JobContext
from panos_core.tools.runtime import ToolStatus

PAGE_CHARS: Final = 6_000
MAX_SOURCES: Final = 10
SYSTEM: Final = (
    "You are Panos's research assistant. Answer the user's question using only the numbered "
    "sources provided. Cite every claim with the source number in square brackets, like [1] "
    "or [2][3]. If the sources do not answer the question, say so plainly. Answer in the "
    "language of the question."
)
_CITE_RE: Final = re.compile(r"\[(\d{1,2})\]")
# Footnote markers inside pages ("…9.6 GW.[5]") look like our citations; drop them.
_FOOTNOTE_RE: Final = re.compile(r"\[(?:\d{1,3}|[a-z]|citation needed|note \d+)\]")


class ResearchWorkflow:
    kind = "research"

    async def run(self, ctx: JobContext) -> dict[str, Any]:
        question = str(ctx.job.input.get("question") or ctx.job.objective).strip()
        if not question:
            raise Blocked("research job without a question")
        limit = max(1, min(int(ctx.job.input.get("max_sources", 5)), MAX_SOURCES))
        state = ctx.state

        if "candidates" not in state:
            urls = [str(u) for u in ctx.job.input.get("urls", [])]
            if not urls:
                await ctx.safe_point()
                async with ctx.step(
                    "agent:researcher", "Search the web", tool="browser.search"
                ) as s:
                    result = await ctx.tool(
                        "browser.search",
                        {"query": question[:300], "limit": limit * 2},
                        actor="agent:researcher",
                    )
                    if result.status != ToolStatus.SUCCEEDED:
                        raise Blocked(f"web search failed: {result.error}")
                    urls = [r["url"] for r in (result.output or {}).get("results", [])]
                    s.output = f"{len(urls)} result(s)"
            state["candidates"] = _diverse(urls, limit * 2)
            state["sources"] = []
            state["failed"] = []
            await ctx.save()

        done = {s["url"] for s in state["sources"]} | set(state["failed"])
        for url in state["candidates"]:
            if len(state["sources"]) >= limit:
                break
            if url in done:
                continue
            await ctx.safe_point()
            async with ctx.step("agent:researcher", f"Read {url[:150]}", tool="browser.read") as s:
                result = await ctx.tool(
                    "browser.read", {"url": url, "max_chars": PAGE_CHARS}, actor="agent:researcher"
                )
                if result.status == ToolStatus.SUCCEEDED and result.output:
                    out = result.output
                    state["sources"].append(
                        {
                            "n": len(state["sources"]) + 1,
                            "url": out["citation"]["url"],
                            "title": out["title"] or out["citation"]["url"],
                            "text": out["text"],
                            "content_hash": out["citation"]["content_hash"],
                            "retrieved_at": out["citation"]["retrieved_at"],
                        }
                    )
                    s.output = out["title"][:200]
                else:
                    state["failed"].append(url)
                    s.output = f"skipped: {result.error}"
            await ctx.save()

        sources = state["sources"]
        if not sources:
            raise Blocked("no source could be read", {"failed": state["failed"]})
        await ctx.safe_point()
        async with ctx.step("agent:researcher", f"Answer from {len(sources)} source(s)") as s:
            answer, provider, model = await self._answer(ctx, question, sources)
            s.output = f"{provider}/{model}"
        cited = sorted({int(n) for n in _CITE_RE.findall(answer) if 0 < int(n) <= len(sources)})
        return {
            "success": True,
            "question": question,
            "answer": answer,
            "sources": [{k: v for k, v in src.items() if k != "text"} for src in sources],
            "cited": cited,
            "uncited_answer": not cited,
            "failed": state["failed"],
            "provider": provider,
            "model": model,
        }

    async def _answer(
        self, ctx: JobContext, question: str, sources: list[dict[str, Any]]
    ) -> tuple[str, str, str]:
        retrieved = [
            RetrievedItem(
                text=f"[{src['n']}] {src['title']}\n{_FOOTNOTE_RE.sub('', src['text'])}",
                source=src["url"],
                privacy=PrivacyMode.STANDARD,  # public web pages
                scope="external",
                score=float(len(sources) - i),
            )
            for i, src in enumerate(sources)
        ]
        messages = build_messages(
            system=SYSTEM,
            user_text=question,
            user_privacy=ctx.privacy,
            retrieved=retrieved,
            budget_tokens=12_000,
        )
        request = ChatRequest(
            messages=messages,
            task=TaskType.SUMMARIZE,
            complexity=Complexity.MEDIUM,
            max_output_tokens=1500,
        )
        result = await ctx.services.gateway.complete(
            request,
            CallContext(
                actor="agent:researcher",
                job_id=ctx.job.id,
                project_id=ctx.project.id if ctx.project else None,
            ),
        )
        return result.response.text, result.decision.provider_id, result.decision.model


def _diverse(urls: list[str], limit: int) -> list[str]:
    """Prefer one page per site, then fill up with the rest (in search order)."""
    seen_hosts: set[str] = set()
    first: list[str] = []
    rest: list[str] = []
    for url in dict.fromkeys(urls):
        host = urlsplit(url).hostname or ""
        (rest if host in seen_hosts else first).append(url)
        seen_hosts.add(host)
    return (first + rest)[:limit]
