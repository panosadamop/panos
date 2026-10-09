"""AI gateway: routing + redaction + adapter guard + metering + audit, with mock providers."""

from datetime import UTC, datetime
from typing import Any

import pytest

from panos_core.ai.gateway import AIGateway, CallContext
from panos_core.ai.ports import ProviderError
from panos_core.ai.privacy import PrivacyViolationError
from panos_core.ai.registry import ModelInfo, ModelRegistry
from panos_core.ai.router import NoRouteError, RouteDecision
from panos_core.ai.types import ChatRequest, ChatResponse, Locality, Message, Role
from panos_core.audit import AuditService
from panos_core.config.settings import AISettings
from panos_core.domain.enums import PrivacyMode
from panos_core.nodes import ensure_local_node
from panos_core.persistence import UnitOfWorkFactory
from tests.ai_fakes import CLOUD_CHAT, LOCAL_CHAT, LOCAL_EMBED, MockAI

pytestmark = pytest.mark.req("FR-021", "BR-003")

S, L = PrivacyMode.STANDARD, PrivacyMode.LOCAL_ONLY
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
FAKE_KEY = "sk-ant-" + "z" * 32  # assembled at runtime: not a real credential


@pytest.fixture
async def audit(uow_factory: UnitOfWorkFactory) -> AuditService:
    node = await ensure_local_node(uow_factory)
    return AuditService(uow_factory, node.id)


@pytest.fixture
def ai() -> MockAI:
    return MockAI()


def make_gateway(
    ai: MockAI, uow_factory: UnitOfWorkFactory, audit: AuditService, **settings: Any
) -> AIGateway:
    return AIGateway(
        dict(ai.providers), ai.models, uow_factory, audit, AISettings(providers=[], **settings)
    )


def chat(privacy: PrivacyMode, text: str = "hello") -> ChatRequest:
    return ChatRequest(
        messages=(
            Message(role=Role.SYSTEM, content="sys", privacy=S),
            Message(role=Role.USER, content=text, privacy=privacy),
        )
    )


@pytest.mark.req("BR-004", "NFR-SEC-005", "AC-US005-02")
async def test_local_only_goes_local_and_cloud_never_sees_it(
    ai: MockAI, uow_factory: UnitOfWorkFactory, audit: AuditService
) -> None:
    gateway = make_gateway(ai, uow_factory, audit)
    result = await gateway.complete(chat(L), CallContext(project_id=None))
    assert result.decision.locality == Locality.LOCAL
    assert result.decision.model == LOCAL_CHAT
    assert ai.cloud.calls == [] and len(ai.local.calls) == 1
    assert ai.local.calls[0][0].redacted is False  # local calls are not rewritten
    assert await audit.list(event_type="ai.cloud_call") == []


@pytest.mark.req("NFR-SEC-004", "FR-023", "NFR-OBS-002")
async def test_standard_goes_to_cloud_redacted_metered_and_audited(
    ai: MockAI, uow_factory: UnitOfWorkFactory, audit: AuditService
) -> None:
    gateway = make_gateway(ai, uow_factory, audit)
    result = await gateway.complete(
        chat(S, f"use key {FAKE_KEY} please"), CallContext(actor="user")
    )
    assert result.decision.model == CLOUD_CHAT
    sent, _ = ai.cloud.calls[0]
    assert sent.redacted and FAKE_KEY not in sent.messages[1].content
    async with uow_factory() as uow:
        usage = await uow.ai_usage.since(EPOCH)
    assert len(usage) == 1 and usage[0].locality == "cloud" and usage[0].cost_usd > 0
    events = await audit.list(event_type="ai.cloud_call")
    assert len(events) == 1 and events[0].metadata["model"] == CLOUD_CHAT
    assert FAKE_KEY not in str(events[0].metadata)


@pytest.mark.req("BR-027", "US-024", "AC-US024-02")
async def test_offline_cloud_falls_back_to_local_without_loops(
    ai: MockAI, uow_factory: UnitOfWorkFactory, audit: AuditService
) -> None:
    gateway = make_gateway(ai, uow_factory, audit)
    await gateway.refresh_health()
    ai.cloud.available = False  # goes down after the health check: retried once locally
    result = await gateway.complete(chat(S), CallContext())
    assert result.decision.locality == Locality.LOCAL
    assert gateway.offline
    # Subsequent requests route locally straight away.
    again = await gateway.complete(chat(S), CallContext())
    assert again.decision.locality == Locality.LOCAL
    ai.local.available = False
    gateway.mark_unhealthy("local", "down")
    with pytest.raises(NoRouteError):
        await gateway.complete(chat(S), CallContext())


async def test_non_retryable_errors_propagate(
    ai: MockAI, uow_factory: UnitOfWorkFactory, audit: AuditService
) -> None:
    def broken(request: ChatRequest, model: str) -> ChatResponse:
        raise ProviderError("bad request", retryable=False)

    ai.local.responder = broken
    with pytest.raises(ProviderError, match="bad request"):
        await make_gateway(ai, uow_factory, audit).complete(chat(L), CallContext())


@pytest.mark.req("AC-US005-04")
async def test_local_only_without_local_model_is_refused_with_explanation(
    ai: MockAI, uow_factory: UnitOfWorkFactory, audit: AuditService
) -> None:
    ai.local.available = False
    with pytest.raises(NoRouteError) as err:
        await make_gateway(ai, uow_factory, audit).complete(chat(L), CallContext())
    assert "local-only" in err.value.summary
    assert ai.cloud.calls == []


@pytest.mark.req("AC-US005-03")
async def test_adapter_refusal_is_audited_as_egress_blocked(
    ai: MockAI, uow_factory: UnitOfWorkFactory, audit: AuditService
) -> None:
    def refuse(request: ChatRequest, model: str) -> ChatResponse:
        raise PrivacyViolationError("blocked at adapter")

    ai.local.responder = refuse
    with pytest.raises(PrivacyViolationError):
        await make_gateway(ai, uow_factory, audit).complete(
            chat(L), CallContext(conversation_id="cnv_x")
        )
    events = await audit.list(event_type="ai.egress_blocked")
    assert len(events) == 1 and events[0].metadata["conversation_id"] == "cnv_x"


@pytest.mark.req("FR-023")
async def test_budget_exhaustion_routes_locally(
    ai: MockAI, uow_factory: UnitOfWorkFactory, audit: AuditService
) -> None:
    gateway = make_gateway(ai, uow_factory, audit, monthly_budget_usd=0.0)
    decision = await gateway.route(chat(S))
    assert decision.locality == Locality.LOCAL
    assert any("budget" in r for _, r in decision.rejected)
    per_request = make_gateway(ai, uow_factory, audit, max_cost_per_request_usd=1e-9)
    assert (await per_request.route(chat(S))).locality == Locality.LOCAL


async def test_stream_yields_decision_then_events_and_meters(
    ai: MockAI, uow_factory: UnitOfWorkFactory, audit: AuditService
) -> None:
    ai.respond(lambda r, m: "one two three")
    gateway = make_gateway(ai, uow_factory, audit)
    items = [i async for i in gateway.stream(chat(L), CallContext())]
    assert isinstance(items[0], RouteDecision) and items[0].locality == Locality.LOCAL
    assert "".join(getattr(i, "text", "") for i in items[1:]).strip() == "one two three"
    async with uow_factory() as uow:
        assert len(await uow.ai_usage.since(EPOCH)) == 1
        assert await uow.ai_usage.cost_since(EPOCH) == 0.0  # local models are free


@pytest.mark.req("AC-US005-02", "NFR-SEC-005")
async def test_embeddings_follow_labels(
    ai: MockAI, uow_factory: UnitOfWorkFactory, audit: AuditService
) -> None:
    # Give the cloud provider an embedding model too: LOCAL_ONLY must still stay local.
    ai.models = ModelRegistry(
        (
            *ai.models.all(),
            ModelInfo(kind="mock-cloud", name="cloud-embed", tier="embedding", context=8192),
        )
    )
    gateway = make_gateway(ai, uow_factory, audit)
    vectors, model = await gateway.embed(["secret design"], [L], CallContext(), query=True)
    assert model.name == LOCAL_EMBED and len(vectors) == 1
    assert ai.local.embed_calls[0][0] == ["q: secret design"]  # query prefix applied
    assert ai.cloud.embed_calls == []
    ai.local.available = False
    gateway.mark_unhealthy("local", "down")
    with pytest.raises(NoRouteError):
        await gateway.embed(["secret design"], [L], CallContext())
    assert ai.cloud.embed_calls == []


async def test_health_cache_and_candidates_require_pulled_local_models(
    ai: MockAI, uow_factory: UnitOfWorkFactory, audit: AuditService
) -> None:
    ai.local.models = (LOCAL_CHAT,)  # embedding model not pulled
    gateway = make_gateway(ai, uow_factory, audit)
    health = await gateway.refresh_health()
    assert health["local"].ok and health["cloud"].ok
    names = {c.model.name for c in gateway.candidates()}
    assert names == {LOCAL_CHAT, CLOUD_CHAT}  # cloud trusted even with an empty model list
    ai.cloud.available = False
    assert (await gateway.refresh_health())["cloud"].ok  # cached
    assert not (await gateway.refresh_health(force=True))["cloud"].ok
    await gateway.close()
