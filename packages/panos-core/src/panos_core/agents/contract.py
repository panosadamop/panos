"""Agent role contract (AGENTS.md, ADR-010).

An agent receives *scoped context, approved facts, permissions and an objective* and returns
a **structured result** (a validated Pydantic model). Agents never act: the orchestrator
turns their results into tool requests that go through policy, approvals and audit.

Handoffs preserve requirement IDs, decisions, open questions, assumptions and provenance.

Models answer in JSON. The reply is parsed strictly (first JSON object, optionally fenced)
and validated; an invalid reply gets exactly one corrective retry, then the call fails.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from panos_core.ai.context import RetrievedItem, build_messages
from panos_core.ai.gateway import AIGateway, CallContext
from panos_core.ai.router import RouteDecision
from panos_core.ai.types import ChatRequest, Complexity, Message, Role, TaskType, Trust
from panos_core.domain.enums import PrivacyMode
from panos_core.domain.errors import DomainError

_FENCE_RE: Final = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
REQ_ID_RE: Final = re.compile(r"\b(?:BR|FR|NFR(?:-[A-Z]+)?|US|AC-US\d{3})-\d{2,3}\b")


class Handoff(BaseModel):
    """What one agent passes to the next (AGENTS.md "Handoffs")."""

    model_config = ConfigDict(extra="forbid")

    requirement_ids: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)

    def merge(self, other: "Handoff") -> "Handoff":
        def union(a: list[str], b: list[str]) -> list[str]:
            return list(dict.fromkeys([*a, *b]))

        return Handoff(
            requirement_ids=union(self.requirement_ids, other.requirement_ids),
            decisions=union(self.decisions, other.decisions),
            open_questions=union(self.open_questions, other.open_questions),
            assumptions=union(self.assumptions, other.assumptions),
            provenance=union(self.provenance, other.provenance),
        )


class AgentTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str
    facts: list[str] = Field(default_factory=list)  # approved facts (trusted)
    permissions: list[str] = Field(default_factory=list)  # tools the orchestrator may run
    context: list[RetrievedItem] = Field(default_factory=list)  # untrusted, labelled
    handoff: Handoff = Field(default_factory=Handoff)
    inputs: dict[str, Any] = Field(default_factory=dict)  # role-specific trusted inputs


@dataclass(frozen=True)
class AgentRole[O: BaseModel]:
    name: str
    instructions: str
    output_model: type[O]
    task: TaskType = TaskType.CODING
    complexity: Complexity = Complexity.MEDIUM
    max_output_tokens: int = 4096


@dataclass
class AgentOutcome[O: BaseModel]:
    result: O
    handoff: Handoff
    decision: RouteDecision | None = None
    attempts: int = 1
    raw: list[str] = field(default_factory=list)


class AgentError(DomainError):
    def __init__(self, role: str, message: str) -> None:
        super().__init__(f"{role}: {message}")
        self.role = role


def extract_json(text: str) -> Any:
    """The first JSON object in ``text`` (fenced or bare); raises ValueError if none."""
    fenced = _FENCE_RE.search(text)
    candidates = [fenced.group(1)] if fenced else []
    start = text.find("{")
    if start != -1:
        candidates.append(text[start:])
    # strict=False accepts raw control characters (e.g. newlines) inside strings, which
    # small local models emit when they put code in a JSON field (live M5 smoke).
    decoder = json.JSONDecoder(strict=False)
    for candidate in candidates:
        try:
            value, _ = decoder.raw_decode(candidate.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("no JSON object found in the reply")


def _system(role: AgentRole[Any], task: AgentTask) -> str:
    schema = json.dumps(role.output_model.model_json_schema(), ensure_ascii=False)
    parts = [
        f"You are the {role.name} agent inside Panos, a local engineering assistant.",
        role.instructions,
        "You never execute actions yourself; Panos runs approved tools on your behalf.",
        "Reply with ONE JSON object only, no prose, matching this JSON Schema:",
        schema,
    ]
    if task.permissions:
        parts.append("Tools Panos may run for this task: " + ", ".join(task.permissions))
    return "\n\n".join(parts)


def _user(task: AgentTask) -> str:
    lines = [f"Objective: {task.objective}"]
    if task.facts:
        lines.append("Approved facts:\n" + "\n".join(f"- {f}" for f in task.facts))
    h = task.handoff
    for title, values in (
        ("Requirement IDs", h.requirement_ids),
        ("Decisions", h.decisions),
        ("Assumptions", h.assumptions),
        ("Open questions", h.open_questions),
    ):
        if values:
            lines.append(f"{title}:\n" + "\n".join(f"- {v}" for v in values))
    for key, value in task.inputs.items():
        rendered = (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)
        )
        lines.append(f"{key}:\n{rendered}")
    return "\n\n".join(lines)


async def run_agent[O: BaseModel](
    role: AgentRole[O],
    task: AgentTask,
    *,
    gateway: AIGateway,
    ctx: CallContext,
    privacy: PrivacyMode,
    budget_tokens: int = 12_000,
) -> AgentOutcome[O]:
    """One structured agent call through the gateway (privacy routing applies)."""
    messages = list(
        build_messages(
            system=_system(role, task),
            user_text=_user(task),
            user_privacy=privacy,
            retrieved=list(task.context),
            budget_tokens=budget_tokens,
        )
    )
    raw: list[str] = []
    decision: RouteDecision | None = None
    for attempt in (1, 2):
        request = ChatRequest(
            messages=tuple(messages),
            task=role.task,
            complexity=role.complexity,
            max_output_tokens=role.max_output_tokens,
        )
        result = await gateway.complete(request, ctx)
        decision = result.decision
        text = result.response.text
        raw.append(text)
        try:
            parsed = role.output_model.model_validate(extract_json(text))
        except (ValueError, ValidationError) as exc:
            if attempt == 2:
                excerpt = " ".join(text.split())[:240]
                raise AgentError(
                    role.name, f"invalid structured output: {exc}; reply began: {excerpt!r}"
                ) from exc
            messages += [
                Message(role=Role.ASSISTANT, content=text, privacy=privacy),
                Message(
                    role=Role.USER,
                    privacy=PrivacyMode.STANDARD,
                    trust=Trust.TRUSTED,
                    content=f"That reply was not valid ({str(exc)[:500]}). Reply again "
                    "with ONE JSON object matching the schema, nothing else.",
                ),
            ]
            continue
        handoff = task.handoff.merge(_handoff_of(parsed, task))
        return AgentOutcome(parsed, handoff, decision, attempt, raw)
    raise AgentError(role.name, "unreachable")  # pragma: no cover


def _handoff_of(result: BaseModel, task: AgentTask) -> Handoff:
    data = result.model_dump()
    found = sorted(set(REQ_ID_RE.findall(task.objective + " " + json.dumps(data))))
    return Handoff(
        requirement_ids=found,
        decisions=[str(d) for d in data.get("decisions", [])],
        open_questions=[str(q) for q in data.get("open_questions", [])],
        assumptions=[str(a) for a in data.get("assumptions", [])],
        provenance=[item.source for item in task.context],
    )
