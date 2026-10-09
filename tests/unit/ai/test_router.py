"""Model router: hard filters first (privacy above all), then tier preference (AI_ROUTING.md)."""

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from panos_core.ai.registry import ModelInfo, ModelRegistry, Tier
from panos_core.ai.router import (
    Candidate,
    NoRouteError,
    Router,
    RouteRequest,
    estimate_tokens,
    task_family,
)
from panos_core.ai.types import ChatRequest, Complexity, Locality, Message, Role, TaskType
from panos_core.domain.enums import PrivacyMode

pytestmark = pytest.mark.req("FR-021", "BR-003")

ALL_TIERS: list[Tier] = [
    "local_light",
    "local_capable",
    "local_large",
    "cloud_light",
    "cloud_efficient",
    "cloud_premium",
    "embedding",
]


def model(name: str, tier: Tier, context: int = 32_000, **kw: Any) -> ModelInfo:
    return ModelInfo(kind="k", name=name, tier=tier, context=context, **kw)


def cand(name: str, tier: Tier, locality: Locality, **kw: Any) -> Candidate:
    return Candidate(f"p-{name}", locality, model(name, tier, **kw))


LOCAL = cand("local7b", "local_capable", Locality.LOCAL, tool_use="json_in_content")
LIGHT = cand("local3b", "local_light", Locality.LOCAL, tool_use="native", gen_tps=40)
CLOUD = cand(
    "sonnet",
    "cloud_efficient",
    Locality.CLOUD,
    cost_in=2.0,
    cost_out=10.0,
    tool_use="native",
    context=1_000_000,
)
PREMIUM = cand(
    "opus",
    "cloud_premium",
    Locality.CLOUD,
    cost_in=4.0,
    cost_out=20.0,
    tool_use="native",
    context=1_000_000,
)
EMBED = cand("embed", "embedding", Locality.LOCAL)


def req(privacy: PrivacyMode = PrivacyMode.STANDARD, **kw: object) -> RouteRequest:
    base: dict[str, object] = {
        "task": TaskType.CHAT,
        "complexity": Complexity.MEDIUM,
        "privacy": privacy,
        "input_tokens": 500,
        "output_tokens": 500,
    }
    base.update(kw)
    return RouteRequest(**base)  # type: ignore[arg-type]


def test_task_families() -> None:
    assert task_family(TaskType.INTENT, Complexity.HIGH) == "intent"
    assert task_family(TaskType.EMBED, Complexity.LOW) == "embed"
    assert task_family(TaskType.CODING, Complexity.HIGH) == "hard"
    assert task_family(TaskType.CODING, Complexity.MEDIUM) == "work"
    assert task_family(TaskType.SUMMARIZE, Complexity.MEDIUM) == "light"
    assert task_family(TaskType.CHAT, Complexity.LOW) == "light"
    assert task_family(TaskType.CHAT, Complexity.MEDIUM) == "chat"
    assert task_family(TaskType.VISION, Complexity.MEDIUM) == "vision"


def test_standard_chat_prefers_efficient_cloud_and_explains() -> None:
    decision = Router([LOCAL, LIGHT, CLOUD, PREMIUM]).route(req())
    assert decision.candidate is CLOUD
    assert any("chose p-sonnet/sonnet" in r for r in decision.reasons)
    assert decision.est_cost_usd > 0


@pytest.mark.req("BR-004", "NFR-SEC-005", "AC-US005-02", "NFR-PRV-004")
def test_local_only_never_routes_to_cloud_and_records_why() -> None:
    decision = Router([CLOUD, PREMIUM, LOCAL]).route(req(PrivacyMode.LOCAL_ONLY))
    assert decision.locality == Locality.LOCAL
    rejected = dict(decision.rejected)
    assert "LOCAL_ONLY" in rejected["p-sonnet/sonnet"]
    assert "LOCAL_ONLY" in rejected["p-opus/opus"]


@pytest.mark.req("AC-US005-04")
def test_no_local_model_means_explained_refusal_not_cloud() -> None:
    with pytest.raises(NoRouteError) as err:
        Router([CLOUD, PREMIUM]).route(req(PrivacyMode.LOCAL_ONLY))
    assert "local-only" in err.value.summary
    assert {k for k, _ in err.value.rejected} == {"p-sonnet/sonnet", "p-opus/opus"}


def test_hard_filters_capability_context_budget_premium() -> None:
    with pytest.raises(NoRouteError):
        Router([LOCAL, CLOUD]).route(req(task=TaskType.EMBED))
    assert Router([LOCAL, EMBED]).route(req(task=TaskType.EMBED)).candidate is EMBED
    # tools: a model without tool support is filtered out
    no_tools = cand("plain", "cloud_efficient", Locality.CLOUD)
    assert Router([no_tools, LOCAL]).route(req(needs_tools=True)).candidate is LOCAL
    # context fit
    big = req(input_tokens=100_000)
    assert Router([LOCAL, CLOUD]).route(big).candidate is CLOUD
    # per-request cost and monthly budget
    assert Router([LOCAL, CLOUD]).route(req(max_cost_usd=0.000001)).candidate is LOCAL
    decision = Router([LOCAL, CLOUD]).route(req(remaining_monthly_usd=0.0))
    assert decision.candidate is LOCAL
    assert "budget" in dict(decision.rejected)["p-sonnet/sonnet"]
    # premium gate
    hard = req(complexity=Complexity.HIGH)
    assert Router([PREMIUM, CLOUD]).route(hard).candidate is PREMIUM
    assert (
        Router([PREMIUM, CLOUD])
        .route(req(complexity=Complexity.HIGH, allow_premium=False))
        .candidate
        is CLOUD
    )


def test_light_tasks_prefer_local_light_then_latency() -> None:
    assert Router([CLOUD, LOCAL, LIGHT]).route(req(task=TaskType.INTENT)).candidate is LIGHT
    fast = cand("fast3b", "local_light", Locality.LOCAL, gen_tps=200)
    assert Router([LIGHT, fast]).route(req(task=TaskType.SUMMARIZE)).candidate is fast


def test_for_chat_derives_privacy_and_size() -> None:
    request = ChatRequest(
        messages=(
            Message(role=Role.SYSTEM, content="sys", privacy=PrivacyMode.STANDARD),
            Message(role=Role.USER, content="x" * 3000, privacy=PrivacyMode.LOCAL_ONLY),
        )
    )
    rr = RouteRequest.for_chat(request, max_cost_usd=1.0)
    assert rr.privacy == PrivacyMode.LOCAL_ONLY
    assert rr.input_tokens == estimate_tokens("sys" + "x" * 3000)
    assert rr.max_cost_usd == 1.0


def test_registry_defaults_and_speed_ema() -> None:
    registry = ModelRegistry()
    names = {m.name for m in registry.all()}
    assert {"qwen2.5:3b", "nomic-embed-text", "claude-sonnet-5-5", "claude-opus-5-5"} <= names
    assert registry.get("ollama", "nomic-embed-text:latest") is not None
    before = registry.get("ollama", "qwen2.5:3b")
    assert before is not None
    gen = before.gen_tps
    registry.update_speed("ollama", "qwen2.5:3b", 0, gen + 10)
    after = registry.get("ollama", "qwen2.5:3b")
    assert after is not None and after.gen_tps == round(0.7 * gen + 0.3 * (gen + 10), 2)
    assert ModelRegistry().get("ollama", "qwen2.5:3b").gen_tps == gen  # type: ignore[union-attr]
    registry.update_speed("ollama", "missing", 1, 1)  # unknown model: ignored


candidate_st = st.builds(
    lambda i, tier, loc, ctx, cost: cand(
        f"m{i}", tier, loc, context=ctx, cost_in=cost, cost_out=cost, tool_use="native"
    ),
    st.integers(0, 10_000),
    st.sampled_from(ALL_TIERS),
    st.sampled_from(list(Locality)),
    st.integers(1_000, 1_000_000),
    st.floats(0, 50),
)


@pytest.mark.req("NFR-SEC-005", "AC-US005-02", "BR-004")
@settings(max_examples=300, deadline=None)
@given(
    candidates=st.lists(candidate_st, max_size=8),
    task=st.sampled_from(list(TaskType)),
    complexity=st.sampled_from(list(Complexity)),
    tokens=st.integers(1, 300_000),
    tools=st.booleans(),
    prefer_cloud=st.booleans(),
    vision=st.booleans(),
)
def test_property_local_only_requests_never_get_a_cloud_model(
    candidates: list[Candidate],
    task: TaskType,
    complexity: Complexity,
    tokens: int,
    tools: bool,
    prefer_cloud: bool,
    vision: bool,
) -> None:
    request = req(
        PrivacyMode.LOCAL_ONLY,
        task=task,
        complexity=complexity,
        input_tokens=tokens,
        needs_tools=tools,
        prefer_cloud=prefer_cloud,  # a preference never overrides the privacy filter
        needs_vision=vision,
    )
    try:
        decision = Router(candidates).route(request)
    except NoRouteError:
        return
    assert decision.locality == Locality.LOCAL
