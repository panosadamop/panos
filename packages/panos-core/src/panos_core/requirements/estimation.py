"""Initial estimation and sprint planning (BR-014, M6-08).

Story points come from each story's ``points`` attribute (missing estimates use the
configured default and lower the confidence). Hours = points × hours-per-point, split across
roles by the configured shares and priced with the configured hourly rates.
"""

from dataclasses import dataclass, field
from typing import Any, Final

from panos_core.config.settings import EstimationSettings
from panos_core.domain.requirements import Requirement, RequirementType

PRIORITY_ORDER: Final = {"must": 0, "should": 1, "could": 2, "wont": 3}


@dataclass
class StoryEstimate:
    key: str
    title: str
    points: int
    estimated: bool  # False = default points were assumed
    priority: str
    hours: float
    person_days: float


@dataclass
class Sprint:
    number: int
    stories: list[str] = field(default_factory=list)
    points: int = 0


@dataclass
class Estimate:
    stories: list[StoryEstimate]
    total_points: int
    total_hours: float
    person_days: float
    by_role: dict[str, dict[str, float]]  # role → {hours, rate, cost}
    total_cost: float
    currency: str
    confidence: str  # high | medium | low
    confidence_score: float
    sprints: list[Sprint]
    assumptions: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "stories": [s.__dict__ for s in self.stories],
            "total_points": self.total_points,
            "total_hours": self.total_hours,
            "person_days": self.person_days,
            "by_role": self.by_role,
            "total_cost": self.total_cost,
            "currency": self.currency,
            "confidence": self.confidence,
            "confidence_score": self.confidence_score,
            "sprints": [s.__dict__ for s in self.sprints],
            "assumptions": self.assumptions,
        }


def estimate(
    items: list[Requirement], cfg: EstimationSettings, *, open_questions: int = 0
) -> Estimate:
    stories = [i for i in items if i.type == RequirementType.US]
    rows: list[StoryEstimate] = []
    for story in sorted(stories, key=lambda s: s.key):
        raw = story.attributes.get("points")
        estimated = isinstance(raw, int | float) and raw > 0
        points = int(raw) if estimated else cfg.default_points  # type: ignore[arg-type]
        hours = round(points * cfg.hours_per_point, 1)
        rows.append(
            StoryEstimate(
                key=story.key,
                title=story.title,
                points=points,
                estimated=estimated,
                priority=str(story.attributes.get("priority", "should")).lower(),
                hours=hours,
                person_days=round(hours / cfg.hours_per_day, 2),
            )
        )
    total_points = sum(r.points for r in rows)
    total_hours = round(sum(r.hours for r in rows), 1)
    share_total = sum(cfg.role_split.values()) or 1.0
    by_role: dict[str, dict[str, float]] = {}
    for role, share in cfg.role_split.items():
        hours = round(total_hours * share / share_total, 1)
        rate = cfg.role_rates.get(role, 0.0)
        by_role[role] = {"hours": hours, "rate": rate, "cost": round(hours * rate, 2)}
    total_cost = round(sum(r["cost"] for r in by_role.values()), 2)

    # Confidence: share of stories with their own estimate, minus open questions/stale.
    estimated_share = (sum(r.estimated for r in rows) / len(rows)) if rows else 0.0
    stale = sum(1 for i in items if i.stale)
    score = max(0.0, round(estimated_share - 0.05 * open_questions - 0.05 * stale, 2))
    confidence = "high" if score >= 0.8 else "medium" if score >= 0.5 else "low"

    sprints: list[Sprint] = []
    current = Sprint(1)
    ordered = sorted(rows, key=lambda r: (PRIORITY_ORDER.get(r.priority, 1), r.key))
    for row in ordered:
        if row.priority == "wont":
            continue
        if current.stories and current.points + row.points > cfg.velocity_points:
            sprints.append(current)
            current = Sprint(current.number + 1)
        current.stories.append(row.key)
        current.points += row.points
    if current.stories:
        sprints.append(current)

    assumptions = [
        f"{cfg.hours_per_point:g} h per story point, {cfg.hours_per_day:g} h per person-day",
        f"velocity {cfg.velocity_points} points per {cfg.sprint_weeks}-week sprint",
    ]
    defaulted = [r.key for r in rows if not r.estimated]
    if defaulted:
        assumptions.append(
            f"{len(defaulted)} unestimated stor{'y' if len(defaulted) == 1 else 'ies'} "
            f"counted as {cfg.default_points} points: {', '.join(defaulted)}"
        )
    return Estimate(
        stories=rows,
        total_points=total_points,
        total_hours=total_hours,
        person_days=round(total_hours / cfg.hours_per_day, 2),
        by_role=by_role,
        total_cost=total_cost,
        currency=cfg.currency,
        confidence=confidence,
        confidence_score=score,
        sprints=sprints,
        assumptions=assumptions,
    )
