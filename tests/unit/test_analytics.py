"""Model/cost analytics aggregation (M10-06, BR-014, FR-021)."""

from datetime import UTC, datetime

import pytest

from panos_core.analytics import summarise
from panos_core.domain.enums import PrivacyMode
from panos_core.domain.ids import new_id
from panos_core.domain.models import AIUsage

pytestmark = pytest.mark.req("BR-014", "FR-021", "NFR-OBS-002")

L, S = PrivacyMode.LOCAL_ONLY, PrivacyMode.STANDARD
PA, PB = new_id("project"), new_id("project")
J1, J2 = new_id("job"), new_id("job")


def usage(
    day: int,
    provider: str,
    model: str,
    locality: str,
    cost: float,
    latency: int,
    project: str | None = None,
    job: str | None = None,
) -> AIUsage:
    return AIUsage(
        provider=provider,
        model=model,
        locality=locality,
        task="chat",
        privacy=L if locality == "local" else S,
        input_tokens=100,
        output_tokens=50,
        cost_usd=cost,
        latency_ms=latency,
        project_id=project,
        job_id=job,
        created_at=datetime(2026, 10, day, 12, tzinfo=UTC),
    )


def test_breakdowns_shares_latency_and_budget() -> None:
    rows = [
        usage(1, "ollama", "qwen", "local", 0.0, 4000, PA, J1),
        usage(1, "ollama", "qwen", "local", 0.0, 6000, PA),
        usage(2, "anthropic", "claude", "cloud", 0.03, 900, PB, J2),
        usage(3, "anthropic", "claude", "cloud", 0.01, 1100),
    ]
    out = summarise(
        rows,
        since=datetime(2026, 9, 12, tzinfo=UTC),
        month_start=datetime(2026, 10, 2, tzinfo=UTC),
        monthly_budget_usd=0.1,
        project_names={PA: "shop"},
    )
    assert out["calls"] == 4 and out["cost_usd"] == 0.04 and out["local_share"] == 0.5
    assert out["budget"] == {"monthly_usd": 0.1, "month_cost_usd": 0.04, "used_share": 0.4}
    assert [d["date"] for d in out["daily"]] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert out["by_model"][0]["name"] == "anthropic/claude" and out["by_model"][0]["calls"] == 2
    assert {p["name"] for p in out["by_project"]} == {"shop", PB, "—"}
    assert [j["name"] for j in out["by_job"]] == [J2, J1]
    assert out["latency_ms"]["max"] == 6000 and 900 <= out["latency_ms"]["p50"] <= 6000
    empty = summarise(
        [],
        since=datetime(2026, 9, 1, tzinfo=UTC),
        month_start=datetime(2026, 10, 1, tzinfo=UTC),
        monthly_budget_usd=None,
    )
    assert empty["local_share"] is None and empty["budget"]["used_share"] is None
