"""Model router (AI_ROUTING.md, FR-021, ADR-009).

1. **Hard filters**, applied before any scoring and never traded off:
   privacy (LOCAL_ONLY → local models only), provider health/offline state, capability
   (tools, vision, embeddings), context fit, budget (per request and monthly), premium gate.
2. **Preference**: the tier order for the task and complexity (AI_ROUTING tiers), then
   estimated latency from measured throughput, then estimated cost.
3. Every decision carries its reasons and the rejected candidates, so "why this model"
   and "why no model" are always explainable (AC-US005-04).
"""

from dataclasses import dataclass, field
from typing import Final

from panos_core.ai.registry import ModelInfo, Tier
from panos_core.ai.types import ChatRequest, Complexity, Locality, TaskType
from panos_core.domain.enums import PrivacyMode

LOCAL_TIERS: Final = ("local_light", "local_capable", "local_large")

# Preferred tier order per (task family, complexity). Earlier = preferred.
_ORDER: Final[dict[str, tuple[Tier, ...]]] = {
    "intent": ("local_light", "local_capable", "cloud_light", "cloud_efficient"),
    "light": ("local_light", "cloud_light", "cloud_efficient", "local_capable", "local_large"),
    "chat": (
        "cloud_efficient",
        "local_capable",
        "local_light",
        "cloud_light",
        "local_large",
        "cloud_premium",
    ),
    "work": ("cloud_efficient", "cloud_premium", "local_capable", "local_large", "local_light"),
    "hard": ("cloud_premium", "cloud_efficient", "local_large", "local_capable", "local_light"),
    "embed": ("embedding",),
    # Local first (ADR-025): images are analyzed locally unless the user consents to cloud.
    "vision": (
        "local_capable",
        "local_large",
        "local_light",
        "cloud_efficient",
        "cloud_premium",
        "cloud_light",
    ),
}
_WORK_TASKS: Final = frozenset(
    {TaskType.CODING, TaskType.REQUIREMENTS, TaskType.ARCHITECTURE, TaskType.REVIEW}
)


def task_family(task: TaskType, complexity: Complexity) -> str:
    if task == TaskType.INTENT:
        return "intent"
    if task == TaskType.EMBED:
        return "embed"
    if task == TaskType.VISION:
        return "vision"
    if complexity == Complexity.HIGH and (task in _WORK_TASKS or task == TaskType.CHAT):
        return "hard"
    if task in _WORK_TASKS:
        return "work"
    if task == TaskType.SUMMARIZE or complexity == Complexity.LOW:
        return "light"
    return "chat"


def estimate_tokens(text: str) -> int:
    """Conservative estimate (Greek and code tokenize denser than English prose)."""
    return max(1, len(text) // 3)


@dataclass(frozen=True)
class Candidate:
    provider_id: str
    locality: Locality
    model: ModelInfo

    @property
    def key(self) -> str:
        return f"{self.provider_id}/{self.model.name}"


@dataclass(frozen=True)
class RouteRequest:
    task: TaskType
    complexity: Complexity
    privacy: PrivacyMode
    input_tokens: int
    output_tokens: int = 1024
    needs_tools: bool = False
    needs_vision: bool = False
    prefer_cloud: bool = False
    max_cost_usd: float | None = None
    remaining_monthly_usd: float | None = None
    allow_premium: bool = True

    @classmethod
    def for_chat(
        cls,
        request: ChatRequest,
        *,
        max_cost_usd: float | None = None,
        remaining_monthly_usd: float | None = None,
        allow_premium: bool = True,
    ) -> "RouteRequest":
        text = "".join(m.content for m in request.messages)
        return cls(
            task=request.task,
            complexity=request.complexity,
            privacy=request.privacy,
            input_tokens=estimate_tokens(text),
            output_tokens=min(request.max_output_tokens, 2048),
            needs_tools=bool(request.tools),
            needs_vision=any(m.images for m in request.messages),
            prefer_cloud=request.prefer_cloud,
            max_cost_usd=max_cost_usd,
            remaining_monthly_usd=remaining_monthly_usd,
            allow_premium=allow_premium,
        )


@dataclass(frozen=True)
class RouteDecision:
    candidate: Candidate
    reasons: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]
    est_latency_s: float
    est_cost_usd: float

    @property
    def provider_id(self) -> str:
        return self.candidate.provider_id

    @property
    def model(self) -> str:
        return self.candidate.model.name

    @property
    def locality(self) -> Locality:
        return self.candidate.locality


class NoRouteError(RuntimeError):
    def __init__(self, request: RouteRequest, rejected: list[tuple[str, str]]) -> None:
        if request.privacy == PrivacyMode.LOCAL_ONLY:
            summary = (
                "this request involves local-only content and no suitable local model "
                "is available, so it cannot be sent to a cloud model"
            )
        else:
            summary = "no available model satisfies this request"
        super().__init__(summary)
        self.summary = summary
        self.rejected = tuple(rejected)


@dataclass
class Router:
    candidates: list[Candidate] = field(default_factory=list)

    def route(self, req: RouteRequest) -> RouteDecision:
        family = task_family(req.task, req.complexity)
        order = _ORDER[family]
        eligible: list[tuple[Candidate, float, float]] = []
        rejected: list[tuple[str, str]] = []
        for c in self.candidates:
            reason = self._reject_reason(c, req, order)
            if reason:
                rejected.append((c.key, reason))
                continue
            latency = c.model.estimate_latency(req.input_tokens, req.output_tokens)
            cost = c.model.estimate_cost(req.input_tokens, req.output_tokens)
            eligible.append((c, latency, cost))
        if not eligible:
            raise NoRouteError(req, rejected)

        def rank(e: tuple[Candidate, float, float]) -> tuple[int, int, float, float]:
            cloud_first = 0 if (req.prefer_cloud and e[0].locality == Locality.CLOUD) else 1
            return (
                cloud_first if req.prefer_cloud else 0,
                order.index(e[0].model.tier),
                e[1],
                e[2],
            )

        best, latency, cost = min(eligible, key=rank)
        reasons = [
            f"task family '{family}' prefers tiers {' > '.join(order)}",
            f"chose {best.key} ({best.model.tier}, {best.locality.value})",
            f"privacy {req.privacy.value}",
            f"estimated {latency:.1f}s, ${cost:.4f}",
        ]
        return RouteDecision(
            best, tuple(reasons), tuple(rejected), round(latency, 2), round(cost, 6)
        )

    @staticmethod
    def _reject_reason(c: Candidate, req: RouteRequest, order: tuple[Tier, ...]) -> str | None:
        m = c.model
        if req.privacy == PrivacyMode.LOCAL_ONLY and c.locality != Locality.LOCAL:
            return "privacy: LOCAL_ONLY content cannot use a cloud model"
        if m.tier not in order:
            return f"tier {m.tier} not suitable for this task"
        if req.needs_tools and m.tool_use == "none":
            return "no tool-use support"
        if req.needs_vision and not m.vision:
            return "no vision support"
        if req.input_tokens + req.output_tokens > m.context:
            return f"context too small ({m.context} tokens)"
        if m.tier == "cloud_premium" and not req.allow_premium:
            return "premium models disabled"
        cost = m.estimate_cost(req.input_tokens, req.output_tokens)
        if req.max_cost_usd is not None and cost > req.max_cost_usd:
            return f"estimated cost ${cost:.4f} exceeds per-request limit"
        if req.remaining_monthly_usd is not None and cost > req.remaining_monthly_usd:
            return "monthly AI budget exhausted"
        return None
