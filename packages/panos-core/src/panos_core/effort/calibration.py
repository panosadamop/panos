"""Calibration math (ADR-031 §3): robust ratios of actual to estimated hours.

For each finished story with a baseline: ``ratio = actual hours / estimated hours``.
Per scope (overall, and per technology):
* factor = median of the ratios (robust to one runaway story);
* spread = interquartile range of the ratios;
* bias = mean(ratio − 1) (positive = estimates were too low);
* MAPE = mean |ratio − 1| (how wrong the original estimates were);
* suggested hours-per-point = median of each story's actual hours ÷ points (correct even
  when older baselines used a different hours-per-point).
A scope needs at least ``min_stories`` stories to produce a suggestion.
"""

import statistics
from dataclasses import dataclass, field
from typing import Final

OVERALL: Final = "overall"


@dataclass(frozen=True)
class StoryOutcome:
    project_id: str
    key: str
    technology: str
    points: int
    estimated_hours: float
    actual_hours: float

    @property
    def ratio(self) -> float:
        return self.actual_hours / self.estimated_hours


@dataclass(frozen=True)
class ScopeResult:
    scope: str
    stories: int
    factor: float
    spread: float
    bias: float
    mape: float
    hours_per_point: float
    basis: list[str] = field(default_factory=list)


def _quartiles(values: list[float]) -> tuple[float, float]:
    if len(values) < 2:
        return values[0], values[0]
    q = statistics.quantiles(values, n=4, method="inclusive")
    return q[0], q[2]


def summarise(scope: str, outcomes: list[StoryOutcome]) -> ScopeResult:
    ratios = sorted(o.ratio for o in outcomes)
    low, high = _quartiles(ratios)
    factor = statistics.median(ratios)
    return ScopeResult(
        scope=scope,
        stories=len(ratios),
        factor=round(factor, 3),
        spread=round(high - low, 3),
        bias=round(statistics.fmean(r - 1 for r in ratios), 3),
        mape=round(statistics.fmean(abs(r - 1) for r in ratios), 3),
        hours_per_point=round(statistics.median(o.actual_hours / o.points for o in outcomes), 2),
        basis=sorted(f"{o.project_id}:{o.key}" for o in outcomes),
    )


def calibrate(outcomes: list[StoryOutcome], *, min_stories: int = 5) -> list[ScopeResult]:
    """Overall plus per-technology results for scopes with enough finished stories."""
    usable = [o for o in outcomes if o.estimated_hours > 0 and o.actual_hours > 0 and o.points > 0]
    results: list[ScopeResult] = []
    if len(usable) >= min_stories:
        results.append(summarise(OVERALL, usable))
    by_tech: dict[str, list[StoryOutcome]] = {}
    for o in usable:
        if o.technology and o.technology != "unknown":
            by_tech.setdefault(o.technology, []).append(o)
    for tech, items in sorted(by_tech.items()):
        if len(items) >= min_stories:
            results.append(summarise(f"technology:{tech}", items))
    return results
