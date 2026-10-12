"""Conversation service (FR-002, US-001/002/005, ADR-008/023).

One turn:

1. load or start the conversation; its privacy label is *sticky* — it starts as the
   project's mode (or ``ai.unscoped_privacy``) and once LOCAL_ONLY it never relaxes;
2. rule-based fast path for simple commands (no model, NFR-PERF-002), executed through
   the tool runtime so policy, approvals and audit still apply;
3. otherwise retrieve project knowledge (labelled with the project's privacy mode), build
   the prompt with untrusted envelopes, and call the AI gateway, whose router enforces the
   privacy boundary;
4. persist both messages; the assistant's answer inherits the request's privacy label.
"""

import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final, Protocol

from panos_core.ai.context import RetrievedItem, Scope, build_messages
from panos_core.ai.gateway import AIGateway, CallContext
from panos_core.ai.privacy import max_label
from panos_core.ai.router import RouteDecision
from panos_core.ai.types import ChatRequest, ChatResponse, Complexity, Message, Role, TaskType
from panos_core.apps.service import AppError, AppService
from panos_core.audit import AuditService
from panos_core.config.settings import AISettings
from panos_core.conversation.intent import Intent, classify_intent
from panos_core.domain import Project
from panos_core.domain.clock import utc_now
from panos_core.domain.enums import PrivacyMode
from panos_core.domain.errors import DomainError
from panos_core.domain.models import Conversation, ConversationMessage
from panos_core.events import EventBus
from panos_core.knowledge import KnowledgeService
from panos_core.knowledge.collections import CollectionService
from panos_core.knowledge.index import SearchHit
from panos_core.observability import get_logger
from panos_core.persistence.unit_of_work import UnitOfWorkFactory
from panos_core.projects import ProjectService
from panos_core.projects.service import ProjectError
from panos_core.tools import ToolContext, ToolRuntime
from panos_core.tools.runtime import ToolResult, ToolStatus

log = get_logger(__name__)

SYSTEM_PROMPT: Final = (
    "You are Panos, a personal engineering assistant running on the user's own computer. "
    "Answer concisely and precisely, in the language the user writes in (Greek or English). "
    "When you use the provided context, cite its source path. If you are not sure, say so."
)
HISTORY_TURNS: Final = 20
RETRIEVAL_LIMIT: Final = 8
CONTEXT_BUDGET_TOKENS: Final = 6000


class ContextFactory(Protocol):
    async def __call__(self, project: Project | None, *, actor: str = ...) -> ToolContext: ...


class ScreenAnalyzer(Protocol):
    async def __call__(self, question: str, project: Project | None, actor: str) -> str: ...


class JobStarter(Protocol):
    async def __call__(self, objective: str, project: Project, actor: str) -> str: ...


class ResearchStarter(Protocol):
    async def __call__(self, question: str, project: Project | None, actor: str) -> str: ...


class ConversationError(DomainError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Reply:
    conversation_id: str
    message_id: str
    text: str
    privacy: PrivacyMode
    intent: str
    provider: str | None = None
    model: str | None = None
    locality: str | None = None
    route_reasons: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    tool: ToolResult | None = None
    latency_ms: int = 0


@dataclass
class _Turn:
    conversation: Conversation
    project: Project | None
    intent: Intent
    user_message: ConversationMessage
    started: float = field(default_factory=time.monotonic)
    sources: tuple[str, ...] = ()


class ConversationService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        gateway: AIGateway,
        knowledge: KnowledgeService,
        projects: ProjectService,
        apps: AppService,
        tools: ToolRuntime,
        audit: AuditService,
        events: EventBus,
        settings: AISettings,
        node_id: str,
        context_for: ContextFactory,
        collections: CollectionService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._collections = collections
        self._gateway = gateway
        self._knowledge = knowledge
        self._projects = projects
        self._apps = apps
        self._tools = tools
        self._audit = audit
        self._events = events
        self._settings = settings
        self._node_id = node_id
        self._context_for = context_for
        self._start_job: JobStarter | None = None
        self._start_research: ResearchStarter | None = None
        self._screen: ScreenAnalyzer | None = None

    def attach_screen(self, analyzer: "ScreenAnalyzer") -> None:
        """ "What's on my screen?" → capture + local vision analysis (ADR-025)."""
        self._screen = analyzer

    def attach_job_starter(self, starter: "JobStarter") -> None:
        """Work requests inside a project ("implement …") become coding jobs (M5)."""
        self._start_job = starter

    def attach_research_starter(self, starter: "ResearchStarter") -> None:
        """ "Research …" becomes a research job with cited sources (ADR-027)."""
        self._start_research = starter

    # ------------------------------------------------------------------ queries
    async def get(self, conversation_id: str) -> tuple[Conversation, Sequence[ConversationMessage]]:
        async with self._uow_factory() as uow:
            conversation = await uow.conversations.get(conversation_id)
            if conversation is None:
                raise ConversationError("not_found", f"conversation {conversation_id!r} not found")
            return conversation, await uow.conversations.messages(conversation_id, limit=200)

    async def list(self, *, limit: int = 50) -> Sequence[Conversation]:
        async with self._uow_factory() as uow:
            return await uow.conversations.list(limit=limit)

    # ------------------------------------------------------------------ turns
    async def send(
        self,
        text: str,
        *,
        conversation_id: str | None = None,
        project_ref: str | None = None,
        actor: str = "user",
    ) -> Reply:
        turn = await self._begin(text, conversation_id, project_ref, actor)
        fast = await self._fast_path(turn, actor)
        if fast is not None:
            return fast
        request = await self._build_request(turn, text)
        result = await self._gateway.complete(request, self._call_ctx(turn, actor))
        return await self._finish(turn, result.response, result.decision, request.privacy)

    async def stream(
        self,
        text: str,
        *,
        conversation_id: str | None = None,
        project_ref: str | None = None,
        actor: str = "user",
    ) -> AsyncIterator[dict[str, Any]]:
        """NDJSON-friendly events: ``start``, ``route``, ``text``…, then ``reply``."""
        turn = await self._begin(text, conversation_id, project_ref, actor)
        yield {
            "type": "start",
            "conversation_id": turn.conversation.id,
            "intent": turn.intent.kind,
            "privacy": turn.conversation.privacy.value,
        }
        fast = await self._fast_path(turn, actor)
        if fast is not None:
            yield {"type": "reply", "reply": reply_payload(fast)}
            return
        request = await self._build_request(turn, text)
        decision: RouteDecision | None = None
        async for item in self._gateway.stream(request, self._call_ctx(turn, actor)):
            if isinstance(item, RouteDecision):
                decision = item
                yield {
                    "type": "route",
                    "provider": item.provider_id,
                    "model": item.model,
                    "locality": item.locality.value,
                    "reasons": list(item.reasons),
                }
            elif item.type == "text":
                self._events.publish(
                    "conversation.delta",
                    {"conversation_id": turn.conversation.id, "text": item.text},
                )
                yield {"type": "text", "text": item.text}
            elif item.type == "done" and item.response is not None and decision is not None:
                reply = await self._finish(turn, item.response, decision, request.privacy)
                yield {"type": "reply", "reply": reply_payload(reply)}

    # ------------------------------------------------------------------ steps
    async def _begin(
        self, text: str, conversation_id: str | None, project_ref: str | None, actor: str
    ) -> _Turn:
        text = text.strip()
        if not text:
            raise ConversationError("empty", "message is empty")
        project = await self._project(project_ref) if project_ref else None
        async with self._uow_factory() as uow:
            if conversation_id is not None:
                conversation = await uow.conversations.get(conversation_id)
                if conversation is None:
                    raise ConversationError(
                        "not_found", f"conversation {conversation_id!r} not found"
                    )
                if project is not None and conversation.project_id not in (None, project.id):
                    raise ConversationError(
                        "project_mismatch", "this conversation belongs to another project"
                    )
                if project is None and conversation.project_id is not None:
                    project = await uow.projects.get(conversation.project_id)
                if project is not None and conversation.project_id is None:
                    conversation = conversation.model_copy(update={"project_id": project.id})
            else:
                start = project.privacy_mode if project else self._settings.unscoped_privacy
                conversation = Conversation(
                    project_id=project.id if project else None,
                    title=text[:80],
                    privacy=PrivacyMode(start),
                )
                await uow.conversations.add(conversation)
            # Sticky taint: the project's current mode can only make the conversation stricter.
            labels = [conversation.privacy] + ([project.privacy_mode] if project else [])
            conversation = conversation.model_copy(
                update={"privacy": max_label(labels), "updated_at": utc_now()}
            )
            await uow.conversations.update(conversation)
            message = ConversationMessage(
                conversation_id=conversation.id,
                role="user",
                content=text,
                privacy=conversation.privacy,
            )
            await uow.conversations.add_message(message)
            await uow.commit()
        intent = classify_intent(text)
        self._events.publish(
            "conversation.message",
            {
                "conversation_id": conversation.id,
                "message_id": message.id,
                "role": "user",
                "intent": intent.kind,
            },
        )
        log.info(
            "conversation_turn",
            conversation_id=conversation.id,
            intent=intent.kind,
            privacy=conversation.privacy.value,
        )
        return _Turn(conversation, project, intent, message)

    async def _project(self, ref: str) -> Project:
        try:
            return await self._projects.get(ref)
        except ProjectError as exc:
            raise ConversationError(exc.reason, str(exc)) from exc

    def _call_ctx(self, turn: _Turn, actor: str) -> CallContext:
        return CallContext(
            actor=actor,
            project_id=turn.project.id if turn.project else None,
            conversation_id=turn.conversation.id,
        )

    async def _build_request(self, turn: _Turn, text: str) -> ChatRequest:
        privacy = turn.conversation.privacy
        retrieved: list[RetrievedItem] = []
        project = turn.project
        scoped = project if project is not None and project.knowledge_enabled else None
        hits: list[tuple[SearchHit, Scope]] = []
        try:
            if self._collections is not None:
                # FR-020: the current project first, then company knowledge, then external
                # sources. Each hit keeps its own privacy label for routing (ADR-008).
                hits = [
                    (r.hit, r.scope)
                    for r in await self._collections.search(
                        text,
                        project_id=scoped.id if scoped else None,
                        limit=RETRIEVAL_LIMIT,
                        privacy=scoped.privacy_mode if scoped else PrivacyMode.LOCAL_ONLY,
                    )
                ]
            elif scoped is not None:
                found = await self._knowledge.search(
                    text, project_id=scoped.id, limit=RETRIEVAL_LIMIT, privacy=scoped.privacy_mode
                )
                hits = [(h, "project") for h in found]
        except Exception:
            log.warning("retrieval_failed", project_id=scoped.id if scoped else None, exc_info=True)
            hits = []
        retrieved = [
            RetrievedItem(
                text=h.snippet,
                source=f"{h.uri}#L{h.start_line}-L{h.end_line}",
                privacy=PrivacyMode(h.privacy_mode),
                scope=scope,
                score=h.score,
            )
            for h, scope in hits
        ]
        async with self._uow_factory() as uow:
            stored = await uow.conversations.messages(turn.conversation.id, limit=HISTORY_TURNS + 1)
        history = [
            Message(role=Role(m.role), content=m.content, privacy=m.privacy)
            for m in stored
            if m.id != turn.user_message.id and m.role in ("user", "assistant")
        ]
        messages = build_messages(
            system=SYSTEM_PROMPT,
            user_text=text,
            user_privacy=privacy,
            history=history,
            retrieved=retrieved,
            budget_tokens=CONTEXT_BUDGET_TOKENS,
        )
        turn.sources = tuple(r.source for r in retrieved)
        task = TaskType.CODING if turn.intent.kind == "task" and project else TaskType.CHAT
        return ChatRequest(messages=messages, task=task, complexity=Complexity.MEDIUM)

    async def _finish(
        self, turn: _Turn, response: ChatResponse, decision: RouteDecision, privacy: PrivacyMode
    ) -> Reply:
        # The answer is derived from everything in the request, so it carries its label.
        label = max_label([privacy, turn.conversation.privacy])
        message = ConversationMessage(
            conversation_id=turn.conversation.id,
            role="assistant",
            content=response.text,
            privacy=label,
            provider=decision.provider_id,
            model=decision.model,
        )
        await self._save_reply(turn, message, label)
        reply = Reply(
            conversation_id=turn.conversation.id,
            message_id=message.id,
            text=response.text,
            privacy=label,
            intent=turn.intent.kind,
            provider=decision.provider_id,
            model=decision.model,
            locality=decision.locality.value,
            route_reasons=decision.reasons,
            sources=turn.sources,
            latency_ms=int((time.monotonic() - turn.started) * 1000),
        )
        self._publish_reply(reply)
        return reply

    async def _save_reply(
        self, turn: _Turn, message: ConversationMessage, label: PrivacyMode
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.conversations.add_message(message)
            conversation = turn.conversation.model_copy(
                update={
                    "privacy": max_label([turn.conversation.privacy, label]),
                    "updated_at": utc_now(),
                }
            )
            await uow.conversations.update(conversation)
            await uow.commit()
        turn.conversation = conversation

    def _publish_reply(self, reply: Reply) -> None:
        self._events.publish(
            "conversation.message",
            {
                "conversation_id": reply.conversation_id,
                "message_id": reply.message_id,
                "role": "assistant",
                "intent": reply.intent,
                "provider": reply.provider,
                "model": reply.model,
                "locality": reply.locality,
            },
        )

    # ------------------------------------------------------------------ fast path
    async def _fast_path(self, turn: _Turn, actor: str) -> Reply | None:
        intent = turn.intent
        tool: ToolResult | None = None
        label = turn.conversation.privacy
        if intent.kind == "list_projects":
            projects = await self._projects.list()
            text = (
                "Registered projects:\n"
                + "\n".join(f"- {p.name} ({p.privacy_mode.value}) — {p.path}" for p in projects)
                if projects
                else "No projects are registered yet."
            )
        elif intent.kind == "list_apps":
            apps = [a for a in await self._apps.list() if a.available]
            text = (
                "Installed applications:\n" + "\n".join(f"- {a.name} ({a.key})" for a in apps)
                if apps
                else "No supported applications were found."
            )
        elif intent.kind == "status":
            health = await self._gateway.refresh_health()
            ok = sorted(pid for pid, h in health.items() if h.ok)
            text = (
                "Panos is running. Available model providers: "
                + (", ".join(ok) if ok else "none (offline)")
                + "."
            )
        elif intent.kind == "open_project" and intent.target:
            try:
                project = await self._projects.get(intent.target)
            except ProjectError:
                text = f"I could not find a project called “{intent.target}”."
            else:
                ctx = await self._context_for(project, actor=actor)
                tool = await self._tools.invoke("project.open", {}, ctx)
                text = _tool_text(tool, f"Opening {project.name}.")
        elif intent.kind == "open_app" and intent.target:
            try:
                app = await self._apps.resolve(intent.target)
            except AppError:
                # Maybe it names a project ("open orders-api").
                try:
                    project = await self._projects.get(intent.target)
                except ProjectError:
                    return None  # not a command after all: let the model answer
                ctx = await self._context_for(project, actor=actor)
                tool = await self._tools.invoke("project.open", {}, ctx)
                text = _tool_text(tool, f"Opening {project.name}.")
            else:
                ctx = await self._context_for(turn.project, actor=actor)
                tool = await self._tools.invoke("app.launch", {"app": app.key}, ctx)
                text = _tool_text(tool, f"Launching {app.name}.")
        elif intent.kind == "screen" and self._screen is not None:
            text = await self._screen(turn.user_message.content, turn.project, actor)
        elif intent.kind == "inbox":
            ctx = await self._context_for(turn.project, actor=actor)
            tool = await self._tools.invoke(
                "email.search", {"query": "in:inbox is:unread", "limit": 10}, ctx
            )
            text, label = _inbox_text(tool, label)
        elif intent.kind == "agenda":
            ctx = await self._context_for(turn.project, actor=actor)
            now = datetime.now().astimezone()  # the user's local day
            start = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
                days=1 if intent.target == "tomorrow" else 0
            )
            tool = await self._tools.invoke(
                "calendar.events",
                {"start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat()},
                ctx,
            )
            text, label = _agenda_text(tool, intent.target or "today", label)
        elif intent.kind == "research" and intent.target and self._start_research:
            # A local-only project's context must not reach the web: research runs outside
            # it (the question is the user's own words; sources come from the search).
            scoped = turn.project
            if scoped is not None and scoped.privacy_mode == PrivacyMode.LOCAL_ONLY:
                scoped = None
            job_id = await self._start_research(intent.target, scoped, actor)
            text = (
                f"Started research job {job_id}: I will search the web, read a few sources and "
                f"answer with citations. Follow it with `panos job show {job_id}`."
                + (
                    " It runs outside the local-only project, so no project content is used."
                    if turn.project is not None and scoped is None
                    else ""
                )
            )
        elif intent.kind == "task" and turn.project is not None and self._start_job:
            job_id = await self._start_job(turn.user_message.content, turn.project, actor)
            text = (
                f"Started job {job_id} in {turn.project.name}. I will plan, implement and "
                "test it, and ask before committing. Follow it with `panos job show "
                f"{job_id}`."
            )
        else:
            return None
        message = ConversationMessage(
            conversation_id=turn.conversation.id, role="assistant", content=text, privacy=label
        )
        await self._save_reply(turn, message, label)
        reply = Reply(
            conversation_id=turn.conversation.id,
            message_id=message.id,
            text=text,
            privacy=label,
            intent=intent.kind,
            locality="local",
            route_reasons=("rule-based intent: no model needed",),
            tool=tool,
            latency_ms=int((time.monotonic() - turn.started) * 1000),
        )
        self._publish_reply(reply)
        return reply


def _label(result: ToolResult, current: PrivacyMode) -> PrivacyMode:
    """The reply carries the stricter of the conversation's and the content's labels."""
    content = PrivacyMode((result.output or {}).get("privacy_mode", PrivacyMode.LOCAL_ONLY))
    return max_label([current, content])


def _inbox_text(result: ToolResult, current: PrivacyMode) -> tuple[str, PrivacyMode]:
    if result.status != ToolStatus.SUCCEEDED:
        return _tool_text(result, ""), current
    messages = (result.output or {}).get("messages", [])
    if not messages:
        return "No unread email in your inbox.", _label(result, current)
    lines = [f"- {m['sender']}: {m['subject'] or '(no subject)'}" for m in messages]
    text = f"{len(messages)} unread email(s):\n" + "\n".join(lines)
    return text, _label(result, current)


def _agenda_text(result: ToolResult, when: str, current: PrivacyMode) -> tuple[str, PrivacyMode]:
    if result.status != ToolStatus.SUCCEEDED:
        return _tool_text(result, ""), current
    out = result.output or {}
    events = [e for e in out.get("events", []) if e.get("my_response") != "declined"]
    if not events:
        return f"Nothing on your calendar {when}.", _label(result, current)
    lines = [
        f"- {'all day' if e['all_day'] else e['start'][11:16] + '–' + e['end'][11:16]}"
        f" {e['summary'] or '(no title)'}"
        for e in events
    ]
    text = f"Your calendar {when}:\n" + "\n".join(lines)
    names = {e["id"]: e["summary"] for e in out.get("events", [])}
    for c in out.get("conflicts", []):
        first, second = names.get(c["first"], c["first"]), names.get(c["second"], c["second"])
        text += f"\nConflict: {first} overlaps {second}."
    return text, _label(result, current)


def _tool_text(result: ToolResult, success: str) -> str:
    if result.status == ToolStatus.SUCCEEDED:
        return success
    if result.status == ToolStatus.APPROVAL_REQUIRED:
        return f"This needs your approval first (approval {result.approval_id})."
    return f"I could not do that: {result.error or result.status.value}."


def reply_payload(reply: Reply) -> dict[str, Any]:
    return {
        "conversation_id": reply.conversation_id,
        "message_id": reply.message_id,
        "text": reply.text,
        "privacy": reply.privacy.value,
        "intent": reply.intent,
        "provider": reply.provider,
        "model": reply.model,
        "locality": reply.locality,
        "route_reasons": list(reply.route_reasons),
        "sources": list(reply.sources),
        "tool": reply.tool.model_dump(mode="json") if reply.tool else None,
        "latency_ms": reply.latency_ms,
    }


__all__ = [
    "ConversationError",
    "ConversationService",
    "Reply",
    "ResearchStarter",
    "reply_payload",
]
