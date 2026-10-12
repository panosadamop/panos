"""AI providers, models, usage and routing dry-runs (FR-021/023, AC-US005-04)."""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from panos_core.ai.adapters.factory import api_key_name
from panos_core.ai.router import NoRouteError
from panos_core.ai.types import ChatRequest, Complexity, Message, Role, TaskType
from panos_core.analytics import node_analytics
from panos_core.domain.clock import utc_now
from panos_core.domain.enums import PrivacyMode
from panos_node_api.routers.conversation import ai_error_response
from panos_node_api.routers.deps import LOCAL_USER, Container, raise_for_reason

router = APIRouter(prefix="/ai", tags=["ai"])


class ProviderView(BaseModel):
    id: str
    kind: str
    locality: str
    ok: bool
    detail: str
    models: list[str]
    has_key: bool


class ModelView(BaseModel):
    kind: str
    name: str
    tier: str
    context: int
    tool_use: str
    cost_in: float
    cost_out: float
    prompt_tps: float
    gen_tps: float


class UsageRow(BaseModel):
    provider: str
    model: str
    locality: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class UsageSummary(BaseModel):
    since: str
    total_cost_usd: float
    monthly_budget_usd: float | None
    rows: list[UsageRow]


class RouteDryRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="hello", max_length=200_000)
    task: TaskType = TaskType.CHAT
    complexity: Complexity = Complexity.MEDIUM
    privacy: Literal["LOCAL_ONLY", "STANDARD"] = "LOCAL_ONLY"


class RouteView(BaseModel):
    provider: str
    model: str
    locality: str
    reasons: list[str]
    rejected: list[dict[str, str]]
    est_latency_s: float
    est_cost_usd: float


class KeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=8, max_length=500)


@router.get("/providers", response_model=list[ProviderView])
async def providers(
    container: Container, refresh: Annotated[bool, Query()] = False
) -> list[ProviderView]:
    health = await container.gateway.refresh_health(force=refresh)
    out = []
    for pid, provider in container.gateway.providers.items():
        h = health.get(pid)
        out.append(
            ProviderView(
                id=pid,
                kind=provider.kind,
                locality=provider.locality.value,
                ok=bool(h and h.ok),
                detail=h.detail if h else "",
                models=list(h.models) if h else [],
                has_key=container.secrets.get(api_key_name(pid)) is not None,
            )
        )
    return out


@router.post("/providers/reload", response_model=list[ProviderView])
async def reload_providers(container: Container) -> list[ProviderView]:
    await container.reload_providers()
    return await providers(container)


@router.put("/providers/{provider_id}/key", status_code=204)
async def set_key(provider_id: str, body: KeyRequest, container: Container) -> None:
    _known(container, provider_id)
    container.secrets.set(api_key_name(provider_id), body.key)
    await container.audit.record(
        actor=LOCAL_USER,
        event_type="ai.key_set",
        action="ai.key.set",
        metadata={"provider": provider_id},
    )
    await container.reload_providers()


@router.delete("/providers/{provider_id}/key", status_code=204)
async def clear_key(provider_id: str, container: Container) -> None:
    _known(container, provider_id)
    container.secrets.delete(api_key_name(provider_id))
    await container.audit.record(
        actor=LOCAL_USER,
        event_type="ai.key_cleared",
        action="ai.key.clear",
        metadata={"provider": provider_id},
    )
    await container.reload_providers()


def _known(container: Any, provider_id: str) -> None:
    if provider_id not in {p.id for p in container.settings.ai.providers}:
        raise_for_reason("not_found", f"provider {provider_id!r} is not configured")


@router.get("/models", response_model=list[ModelView])
async def models(container: Container) -> list[ModelView]:
    return [
        ModelView(**m.model_dump(include=set(ModelView.model_fields)))
        for m in container.models.all()
    ]


@router.get("/usage", response_model=UsageSummary)
async def usage(
    container: Container, days: Annotated[int | None, Query(ge=1, le=366)] = None
) -> UsageSummary:
    now = utc_now()
    if days is None:
        since = datetime(now.year, now.month, 1, tzinfo=now.tzinfo)
    else:
        since = datetime.fromtimestamp(now.timestamp() - days * 86400, tz=now.tzinfo)
    async with container.uow_factory() as uow:
        records = await uow.ai_usage.since(since)
    grouped: dict[tuple[str, str, str], UsageRow] = {}
    for r in records:
        key = (r.provider, r.model, r.locality)
        row = grouped.setdefault(
            key,
            UsageRow(
                provider=r.provider,
                model=r.model,
                locality=r.locality,
                calls=0,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
            ),
        )
        row.calls += 1
        row.input_tokens += r.input_tokens
        row.output_tokens += r.output_tokens
        row.cost_usd = round(row.cost_usd + r.cost_usd, 6)
    return UsageSummary(
        since=since.isoformat(),
        total_cost_usd=round(sum(r.cost_usd for r in records), 6),
        monthly_budget_usd=container.settings.ai.monthly_budget_usd,
        rows=list(grouped.values()),
    )


@router.post("/route", response_model=RouteView)
async def route(body: RouteDryRun, container: Container) -> Any:
    """Which model would handle this request, and why — without calling it."""
    request = ChatRequest(
        messages=(Message(role=Role.USER, content=body.text, privacy=PrivacyMode(body.privacy)),),
        task=body.task,
        complexity=body.complexity,
    )
    try:
        decision = await container.gateway.route(request)
    except NoRouteError as exc:
        return ai_error_response(exc)
    return RouteView(
        provider=decision.provider_id,
        model=decision.model,
        locality=decision.locality.value,
        reasons=list(decision.reasons),
        rejected=[{"candidate": k, "reason": r} for k, r in decision.rejected],
        est_latency_s=decision.est_latency_s,
        est_cost_usd=decision.est_cost_usd,
    )


@router.get("/analytics")
async def analytics(
    container: Container, days: Annotated[int, Query(ge=1, le=366)] = 30
) -> dict[str, Any]:
    """Model/cost analytics (M10-06): series, breakdowns, local share, latency, budget burn."""
    data: dict[str, Any] = await node_analytics(
        container.uow_factory, container.projects, container.settings.ai.monthly_budget_usd, days
    )
    return data
