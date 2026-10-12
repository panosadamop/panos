"""Calibration math (ADR-031 §3), golden values."""

import pytest

from panos_core.effort.calibration import OVERALL, StoryOutcome, calibrate

pytestmark = pytest.mark.req("BR-015", "BR-014")


def outcome(
    key: str, points: int, est: float, actual: float, tech: str = "fastapi"
) -> StoryOutcome:
    return StoryOutcome("prj_1", key, tech, points, est, actual)


def test_median_ratio_and_hours_per_point_are_robust() -> None:
    stories = [
        outcome("US-001", 3, 18, 27),  # 1.5
        outcome("US-002", 2, 12, 15),  # 1.25
        outcome("US-003", 5, 30, 45),  # 1.5
        outcome("US-004", 1, 6, 9),  # 1.5
        outcome("US-005", 3, 18, 180),  # 10 (runaway) – must not dominate
    ]
    [overall, tech] = calibrate(stories, min_stories=5)
    assert overall.scope == OVERALL and tech.scope == "technology:fastapi"
    assert overall.factor == 1.5 and overall.stories == 5
    assert overall.hours_per_point == 9.0  # median of 9, 7.5, 9, 9, 60
    assert overall.spread == 0.0  # IQR of [1.25, 1.5, 1.5, 1.5, 10]
    assert overall.bias == round((0.5 + 0.25 + 0.5 + 0.5 + 9) / 5, 3)
    assert overall.mape == overall.bias  # all overruns here
    assert overall.basis[0] == "prj_1:US-001"


def test_thresholds_and_technology_scopes() -> None:
    stories = [
        outcome(f"US-00{i}", 2, 12, 10, "django" if i < 3 else "unknown") for i in range(1, 6)
    ]
    results = calibrate(stories, min_stories=5)
    assert [r.scope for r in results] == [OVERALL]  # 2 django stories < 5; unknown never scoped
    assert results[0].factor == round(10 / 12, 3) and results[0].bias < 0
    assert calibrate(stories[:4], min_stories=5) == []
    zero = [outcome("US-009", 0, 0, 5), *stories]
    assert calibrate(zero, min_stories=5)[0].stories == 5  # unusable rows are ignored
