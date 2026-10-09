"""Agent contract: strict structured output, one retry, handoff preservation (ADR-010)."""

import json
from typing import Any, cast

import pytest

from panos_core.agents import (
    PLANNER,
    AgentError,
    AgentTask,
    Handoff,
    Plan,
    extract_json,
    run_agent,
)
from panos_core.ai.context import RetrievedItem
from panos_core.ai.gateway import AIGateway, CallContext, GatewayResult
from panos_core.ai.router import Candidate, RouteDecision
from panos_core.ai.types import ChatRequest, ChatResponse, Locality, Role, Trust
from panos_core.domain.enums import PrivacyMode
from tests.ai_fakes import MULTIPLY_PLAN, mock_models

pytestmark = pytest.mark.req("BR-007")


class FakeGateway:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest, ctx: CallContext) -> GatewayResult:
        self.requests.append(request)
        model = mock_models().all()[0]
        decision = RouteDecision(Candidate("local", Locality.LOCAL, model), (), (), 0.0, 0.0)
        return GatewayResult(
            ChatResponse(text=self.replies.pop(0), model=model.name, provider="local"), decision
        )


def task(**kw: Any) -> AgentTask:
    return AgentTask(objective="Add multiply for FR-006 and AC-US020-01", **kw)


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ('{"a": 1}', True),
        ('Sure! ```json\n{"a": 1}\n``` done', True),
        ('Plan: {"a": 1} trailing words', True),
        ("no json here", False),
        ("[1, 2]", False),
        ('{"broken": ', False),
        # Raw newline inside a string (seen from qwen2.5-coder in the live M5 smoke).
        ('{"a": 1, "code": "line one\nline two"}', True),
    ],
)
def test_extract_json(text: str, ok: bool) -> None:
    if ok:
        assert extract_json(text)["a"] == 1
    else:
        with pytest.raises(ValueError, match="JSON"):
            extract_json(text)


async def test_valid_reply_and_handoff_carry_ids_decisions_and_provenance() -> None:
    gateway = FakeGateway(json.dumps(MULTIPLY_PLAN))
    item = RetrievedItem("def add(a, b): ...", "calc.py#L1-L2", PrivacyMode.LOCAL_ONLY)
    outcome = await run_agent(
        PLANNER,
        task(context=[item], handoff=Handoff(decisions=["earlier decision"])),
        gateway=cast(AIGateway, gateway),
        ctx=CallContext(),
        privacy=PrivacyMode.LOCAL_ONLY,
    )
    assert isinstance(outcome.result, Plan) and outcome.attempts == 1
    h = outcome.handoff
    assert h.requirement_ids == ["AC-US020-01", "FR-006"]
    assert h.decisions == ["earlier decision", "keep the module-level function style"]
    assert h.assumptions == ["integers only"] and h.provenance == ["calc.py#L1-L2"]
    request = gateway.requests[0]
    assert request.privacy == PrivacyMode.LOCAL_ONLY  # project label travels with the call
    system = request.messages[0].content
    assert "Planner agent" in system and '"steps"' in system  # schema is in the prompt
    untrusted = [m for m in request.messages if m.trust == Trust.UNTRUSTED]
    assert untrusted and "calc.py#L1-L2" in untrusted[0].content


async def test_invalid_reply_gets_one_corrective_retry() -> None:
    gateway = FakeGateway("let me think...", json.dumps(MULTIPLY_PLAN))
    outcome = await run_agent(
        PLANNER,
        task(),
        gateway=cast(AIGateway, gateway),
        ctx=CallContext(),
        privacy=PrivacyMode.STANDARD,
    )
    assert outcome.attempts == 2 and len(outcome.raw) == 2
    retry = gateway.requests[1].messages
    assert retry[-2].role == Role.ASSISTANT and "not valid" in retry[-1].content


async def test_two_invalid_replies_fail_closed() -> None:
    gateway = FakeGateway('{"summary": "x", "steps": []}', "still not json")
    with pytest.raises(AgentError, match="Planner"):
        await run_agent(
            PLANNER,
            task(),
            gateway=cast(AIGateway, gateway),
            ctx=CallContext(),
            privacy=PrivacyMode.STANDARD,
        )


def test_handoff_merge_is_ordered_and_deduplicated() -> None:
    a = Handoff(requirement_ids=["FR-1"], decisions=["x"])
    b = Handoff(requirement_ids=["FR-1", "FR-2"], decisions=["y", "x"])
    merged = a.merge(b)
    assert merged.requirement_ids == ["FR-1", "FR-2"] and merged.decisions == ["x", "y"]
