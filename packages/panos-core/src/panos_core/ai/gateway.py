"""AI gateway: the single path from Panos to any model (ADR-008/009).

route (hard privacy boundary) → redact for cloud → provider call (adapter egress guard)
→ usage metering → audit. A provider that turns out to be unreachable is marked unhealthy
and the request is re-routed once, which gives offline fallback to local models (BR-027).
"""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime

from panos_core.ai.ports import ChatProvider, ProviderError, ProviderHealth
from panos_core.ai.privacy import PrivacyViolationError, max_label, redact_for_cloud
from panos_core.ai.registry import ModelInfo, ModelRegistry
from panos_core.ai.router import Candidate, RouteDecision, Router, RouteRequest
from panos_core.ai.types import (
    ChatRequest,
    ChatResponse,
    Complexity,
    Locality,
    StreamEvent,
    TaskType,
)
from panos_core.audit import AuditService
from panos_core.config.settings import AISettings
from panos_core.domain.clock import utc_now
from panos_core.domain.enums import PrivacyMode
from panos_core.domain.models import AIUsage
from panos_core.observability import get_logger
from panos_core.persistence.unit_of_work import UnitOfWorkFactory

log = get_logger(__name__)

HEALTH_TTL_S = 60.0
HEALTH_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class CallContext:
    actor: str = "system"
    project_id: str | None = None
    conversation_id: str | None = None
    job_id: str | None = None


@dataclass(frozen=True)
class GatewayResult:
    response: ChatResponse
    decision: RouteDecision


class AIGateway:
    def __init__(
        self,
        providers: dict[str, ChatProvider],
        registry: ModelRegistry,
        uow_factory: UnitOfWorkFactory,
        audit: AuditService,
        settings: AISettings,
    ) -> None:
        self.providers = providers
        self.registry = registry
        self._uow_factory = uow_factory
        self._audit = audit
        self._settings = settings
        self._health: dict[str, ProviderHealth] = {}
        self._checked_at = 0.0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ health / candidates
    async def refresh_health(self, *, force: bool = False) -> dict[str, ProviderHealth]:
        async with self._lock:
            if not force and time.monotonic() - self._checked_at < HEALTH_TTL_S and self._health:
                return dict(self._health)

            async def check(pid: str, provider: ChatProvider) -> tuple[str, ProviderHealth]:
                try:
                    return pid, await asyncio.wait_for(provider.health(), HEALTH_TIMEOUT_S)
                except Exception as exc:
                    return pid, ProviderHealth(ok=False, detail=type(exc).__name__)

            results = await asyncio.gather(*(check(p, v) for p, v in self.providers.items()))
            self._health = dict(results)
            self._checked_at = time.monotonic()
            return dict(self._health)

    def replace_providers(self, providers: dict[str, ChatProvider]) -> None:
        self.providers = providers
        self._health = {}
        self._checked_at = 0.0

    def mark_unhealthy(self, provider_id: str, detail: str) -> None:
        self._health[provider_id] = ProviderHealth(ok=False, detail=detail)

    def candidates(self) -> list[Candidate]:
        out: list[Candidate] = []
        for pid, provider in self.providers.items():
            health = self._health.get(pid)
            if health is None or not health.ok:
                continue
            for model in self.registry.for_kind(provider.kind):
                # Local runtimes must actually have the model pulled. Cloud providers list
                # dated IDs next to aliases, so the registry is trusted for them.
                if provider.locality == Locality.LOCAL and model.name not in health.models:
                    continue
                out.append(Candidate(pid, provider.locality, model))
        return out

    @property
    def offline(self) -> bool:
        return not any(
            h.ok
            for pid, h in self._health.items()
            if self.providers[pid].locality == Locality.CLOUD
        )

    async def _month_spend(self) -> float:
        now = utc_now()
        start = datetime(now.year, now.month, 1, tzinfo=now.tzinfo)
        async with self._uow_factory() as uow:
            return await uow.ai_usage.cost_since(start)

    # ------------------------------------------------------------------ routing
    async def route(self, request: ChatRequest) -> RouteDecision:
        await self.refresh_health()
        remaining = None
        if self._settings.monthly_budget_usd is not None:
            remaining = max(0.0, self._settings.monthly_budget_usd - await self._month_spend())
        req = RouteRequest.for_chat(
            request,
            max_cost_usd=self._settings.max_cost_per_request_usd,
            remaining_monthly_usd=remaining,
            allow_premium=self._settings.allow_premium,
        )
        return Router(self.candidates()).route(req)

    @staticmethod
    def prepare(request: ChatRequest, decision: RouteDecision) -> ChatRequest:
        """Redact STANDARD content bound for a cloud model (NFR-SEC-004)."""
        if decision.locality != Locality.CLOUD:
            return request
        messages = tuple(
            m.model_copy(update={"content": redact_for_cloud(m.content)}) for m in request.messages
        )
        return request.model_copy(update={"messages": messages, "redacted": True})

    # ------------------------------------------------------------------ calls
    async def complete(self, request: ChatRequest, ctx: CallContext) -> GatewayResult:
        for attempt in range(2):
            decision = await self.route(request)
            provider = self.providers[decision.provider_id]
            started = time.monotonic()
            try:
                response = await provider.chat(self.prepare(request, decision), decision.model)
            except PrivacyViolationError as exc:
                await self._blocked(decision, request, ctx, str(exc))
                raise
            except ProviderError as exc:
                if exc.retryable and attempt == 0:
                    self.mark_unhealthy(decision.provider_id, str(exc))
                    continue
                raise
            await self._record(decision, request, response, ctx, started)
            return GatewayResult(response, decision)
        raise ProviderError("no provider could complete the request")  # pragma: no cover

    async def stream(
        self, request: ChatRequest, ctx: CallContext
    ) -> AsyncIterator[RouteDecision | StreamEvent]:
        """Yields the RouteDecision first, then StreamEvents (final one has type "done")."""
        decision = await self.route(request)
        yield decision
        provider = self.providers[decision.provider_id]
        started = time.monotonic()
        try:
            async for event in provider.stream(self.prepare(request, decision), decision.model):
                if event.type == "done" and event.response is not None:
                    await self._record(decision, request, event.response, ctx, started)
                yield event
        except PrivacyViolationError as exc:
            await self._blocked(decision, request, ctx, str(exc))
            raise
        except ProviderError as exc:
            if exc.retryable:
                self.mark_unhealthy(decision.provider_id, str(exc))
            raise

    async def embed(
        self,
        texts: Sequence[str],
        labels: Sequence[PrivacyMode],
        ctx: CallContext,
        *,
        query: bool = False,
    ) -> tuple[list[list[float]], ModelInfo]:
        privacy = max_label(labels)
        request = ChatRequest(messages=(), task=TaskType.EMBED, complexity=Complexity.LOW)
        await self.refresh_health()
        req = RouteRequest(
            task=TaskType.EMBED,
            complexity=Complexity.LOW,
            privacy=privacy,
            input_tokens=max((len(t) // 3 for t in texts), default=1),
            output_tokens=0,
        )
        decision = Router(self.candidates()).route(req)
        model = decision.candidate.model
        prefix = model.query_prefix if query else model.doc_prefix
        provider = self.providers[decision.provider_id]
        try:
            vectors = await provider.embed([prefix + t for t in texts], model.name, list(labels))
        except PrivacyViolationError as exc:
            await self._blocked(decision, request, ctx, str(exc))
            raise
        return vectors, model

    # ------------------------------------------------------------------ bookkeeping
    async def _record(
        self,
        decision: RouteDecision,
        request: ChatRequest,
        response: ChatResponse,
        ctx: CallContext,
        started: float,
    ) -> None:
        model = decision.candidate.model
        usage = AIUsage(
            provider=decision.provider_id,
            model=decision.model,
            locality=decision.locality.value,
            task=request.task.value,
            privacy=request.privacy,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=round(
                model.estimate_cost(response.usage.input_tokens, response.usage.output_tokens), 6
            ),
            latency_ms=int((time.monotonic() - started) * 1000),
            project_id=ctx.project_id,
            conversation_id=ctx.conversation_id,
            job_id=ctx.job_id,
        )
        async with self._uow_factory() as uow:
            await uow.ai_usage.add(usage)
            await uow.commit()
        if decision.locality == Locality.CLOUD:
            await self._audit.record(
                actor=ctx.actor,
                event_type="ai.cloud_call",
                action=request.task.value,
                project_id=ctx.project_id,
                job_id=ctx.job_id,
                metadata={
                    "provider": decision.provider_id,
                    "model": decision.model,
                    "privacy": request.privacy,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cost_usd": usage.cost_usd,
                    "conversation_id": ctx.conversation_id,
                },
            )

    async def _blocked(
        self, decision: RouteDecision, request: ChatRequest, ctx: CallContext, detail: str
    ) -> None:
        log.error("ai_egress_blocked", provider=decision.provider_id, model=decision.model)
        await self._audit.record(
            actor=ctx.actor,
            event_type="ai.egress_blocked",
            action=request.task.value,
            project_id=ctx.project_id,
            job_id=ctx.job_id,
            metadata={
                "provider": decision.provider_id,
                "model": decision.model,
                "detail": detail,
                "conversation_id": ctx.conversation_id,
            },
        )

    async def close(self) -> None:
        for provider in self.providers.values():
            try:
                await provider.close()
            except Exception:
                log.warning("provider_close_failed", provider=provider.id)
