"""pytest plugin: requirement traceability for tests (FR-009 applied to Panos itself).

* ``@pytest.mark.req("FR-011", "AC-US041-03")`` tags a test with requirement IDs.
* Unknown IDs fail collection (validated against docs/traceability/requirements.yaml).
* ``--req-report=PATH`` writes a Markdown coverage report after the run.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "docs" / "traceability" / "requirements.yaml"
_ENTRY_RE = re.compile(r"^  - id: (\S+)\n    type: (\S+)\n    title: \"(.*)\"$", re.M)
_TYPE_ORDER = ("BR", "FR", "NFR", "EPIC", "US", "AC")


@dataclass
class _State:
    registry: dict[str, tuple[str, str]] = field(default_factory=dict)  # id -> (type, title)
    tests_by_req: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    outcomes: dict[str, str] = field(default_factory=dict)  # nodeid -> outcome


_STATE_KEY = pytest.StashKey[_State]()


def load_registry(path: Path) -> dict[str, tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    return {m[1]: (m[2], m[3].replace('\\"', '"')) for m in _ENTRY_RE.finditer(text)}


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("req-coverage")
    group.addoption("--req-report", default=None, help="write requirement coverage Markdown")
    group.addoption("--req-registry", default=str(DEFAULT_REGISTRY), help="requirement registry")


def pytest_configure(config: pytest.Config) -> None:
    state = _State(registry=load_registry(Path(config.getoption("--req-registry"))))
    config.stash[_STATE_KEY] = state


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    state = config.stash[_STATE_KEY]
    unknown: list[str] = []
    for item in items:
        for marker in item.iter_markers("req"):
            if not marker.args:
                unknown.append(f"{item.nodeid}: @req without IDs")
            for req_id in marker.args:
                if req_id not in state.registry:
                    unknown.append(f"{item.nodeid}: unknown requirement {req_id!r}")
                else:
                    state.tests_by_req[req_id].append(item.nodeid)
    if unknown:
        raise pytest.UsageError("invalid requirement markers:\n  " + "\n  ".join(unknown))


_REPORTS: list[tuple[str, str, str]] = []  # (nodeid, phase, outcome)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _REPORTS.append((report.nodeid, report.when, report.outcome))


def _final_outcomes() -> dict[str, str]:
    outcomes: dict[str, str] = {}
    for nodeid, when, outcome in _REPORTS:
        if outcome == "failed":
            outcomes[nodeid] = "failed"
        elif outcome == "skipped" and outcomes.get(nodeid) != "failed":
            outcomes[nodeid] = "skipped"
        elif when == "call" and nodeid not in outcomes:
            outcomes[nodeid] = "passed"
    return outcomes


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    target = session.config.getoption("--req-report")
    if not target:
        return
    state = session.config.stash[_STATE_KEY]
    outcomes = _final_outcomes()
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    Path(target).write_text(_render(state, outcomes), encoding="utf-8", newline="\n")


def _status(tests: list[str], outcomes: dict[str, str]) -> str:
    results = {outcomes.get(t, "not run") for t in tests}
    if "failed" in results:
        return "FAILED"
    if results == {"passed"}:
        return "passed"
    if "passed" in results:
        return "partial"
    return "/".join(sorted(results))


def _render(state: _State, outcomes: dict[str, str]) -> str:
    covered = {r: t for r, t in state.tests_by_req.items() if t}
    lines = [
        "# Requirement test coverage",
        "",
        f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `pytest --req-report`. "
        "Do not edit by hand._",
        "",
        "A requirement counts as *covered* when at least one test is tagged with it. "
        "Coverage grows milestone by milestone; untested requirements belong to later milestones.",
        "",
        "| Type | Total | Covered | Passing |",
        "|---|---|---|---|",
    ]
    for kind in _TYPE_ORDER:
        ids = [r for r, (t, _) in state.registry.items() if t == kind]
        cov = [r for r in ids if r in covered]
        ok = [r for r in cov if _status(covered[r], outcomes) == "passed"]
        lines.append(f"| {kind} | {len(ids)} | {len(cov)} | {len(ok)} |")
    lines += [
        "",
        "## Covered requirements",
        "",
        "| ID | Title | Status | Tests |",
        "|---|---|---|---|",
    ]
    for req_id in sorted(covered, key=lambda r: (_TYPE_ORDER.index(state.registry[r][0]), r)):
        _, title = state.registry[req_id]
        tests = covered[req_id]
        short = title if len(title) <= 70 else title[:67] + "..."
        lines.append(f"| {req_id} | {short} | {_status(tests, outcomes)} | {len(tests)} |")
    lines += ["", "## Tests per requirement", ""]
    for req_id in sorted(covered):
        lines.append(f"- **{req_id}**")
        lines += [f"  - `{t}` — {outcomes.get(t, 'not run')}" for t in sorted(covered[req_id])]
    return "\n".join(lines) + "\n"
