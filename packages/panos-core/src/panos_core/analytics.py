"""Model and cost analytics from metered AI calls (M10-06, BR-014, FR-021/027, NFR-OBS-002).

Pure aggregation over :class:`AIUsage` records: totals, a daily series, breakdowns by
provider/model, locality, task, project and job, local-versus-cloud share, latency
percentiles and the month's budget burn. No prompt or response text exists in the records,
so the result can be shared with the hub.
"""

import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any

from panos_core.domain.clock import utc_now
from panos_core.domain.models import AIUsage
from panos_core.persistence import UnitOfWorkFactory
from panos_core.projects import ProjectService


def _bucket(rows: Iterable[AIUsage], key: Any) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        name = key(r) or "—"
        g = groups.setdefault(
            name, {"name": name, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        )
        g["calls"] += 1
        g["input_tokens"] += r.input_tokens
        g["output_tokens"] += r.output_tokens
        g["cost_usd"] = round(g["cost_usd"] + r.cost_usd, 6)
    return sorted(groups.values(), key=lambda g: (-g["cost_usd"], -g["calls"], g["name"]))


def _percentile(values: Sequence[int], q: float) -> int:
    if not values:
        return 0
    if len(values) == 1:
        return values[0]
    return int(statistics.quantiles(sorted(values), n=100, method="inclusive")[int(q) - 1])


def summarise(
    rows: Sequence[AIUsage],
    *,
    since: datetime,
    month_start: datetime,
    monthly_budget_usd: float | None,
    project_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    names = project_names or {}
    total_cost = round(sum(r.cost_usd for r in rows), 6)
    month_cost = round(sum(r.cost_usd for r in rows if r.created_at >= month_start), 6)
    local = sum(1 for r in rows if r.locality == "local")
    latencies = [r.latency_ms for r in rows if r.latency_ms]
    daily: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "cost_usd": 0.0, "tokens": 0}
    )
    for r in rows:
        d = daily[r.created_at.date().isoformat()]
        d["calls"] += 1
        d["cost_usd"] = round(d["cost_usd"] + r.cost_usd, 6)
        d["tokens"] += r.input_tokens + r.output_tokens
    return {
        "since": since.isoformat(),
        "calls": len(rows),
        "input_tokens": sum(r.input_tokens for r in rows),
        "output_tokens": sum(r.output_tokens for r in rows),
        "cost_usd": total_cost,
        "local_share": round(local / len(rows), 3) if rows else None,
        "latency_ms": {
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "max": max(latencies, default=0),
        },
        "budget": {
            "monthly_usd": monthly_budget_usd,
            "month_cost_usd": month_cost,
            "used_share": round(month_cost / monthly_budget_usd, 3) if monthly_budget_usd else None,
        },
        "daily": [{"date": k, **v} for k, v in sorted(daily.items())],
        "by_model": _bucket(rows, lambda r: f"{r.provider}/{r.model}"),
        "by_locality": _bucket(rows, lambda r: r.locality),
        "by_task": _bucket(rows, lambda r: r.task),
        "by_project": _bucket(rows, lambda r: names.get(r.project_id or "", r.project_id or "")),
        "by_job": _bucket([r for r in rows if r.job_id], lambda r: r.job_id)[:20],
    }


async def node_analytics(
    uow_factory: UnitOfWorkFactory,
    projects: ProjectService,
    monthly_budget_usd: float | None,
    days: int = 30,
) -> dict[str, Any]:
    """This node's analytics for the last ``days`` days (budget burn: the calendar month)."""
    now = utc_now()
    since = now - timedelta(days=max(1, min(days, 366)))
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async with uow_factory() as uow:
        rows = await uow.ai_usage.since(min(since, month_start))
    names = {p.id: p.name for p in await projects.list()}
    result = summarise(
        [r for r in rows if r.created_at >= since],
        since=since,
        month_start=month_start,
        monthly_budget_usd=monthly_budget_usd,
        project_names=names,
    )
    month_cost = round(sum(r.cost_usd for r in rows if r.created_at >= month_start), 6)
    result["budget"]["month_cost_usd"] = month_cost
    result["budget"]["used_share"] = (
        round(month_cost / monthly_budget_usd, 3) if monthly_budget_usd else None
    )
    return result
