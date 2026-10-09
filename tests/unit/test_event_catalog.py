"""The desktop's event types are generated from the catalogue and must not drift (M7-02)."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from panos_core.domain.enums import JobStatus
from panos_core.event_catalog import EVENT_TYPES, JOB_STATUSES

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.req("NFR-MNT-001")


def _exporter() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "export_event_types", ROOT / "scripts/export_event_types.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalogue_matches_domain_and_generated_typescript() -> None:
    assert tuple(s.value for s in JobStatus) == JOB_STATUSES
    rendered = _exporter().render()
    generated = (ROOT / "apps/desktop/src/api/events.ts").read_text(encoding="utf-8")
    assert rendered == generated, "run `pnpm gen` (apps/desktop/src/api/events.ts is stale)"
    for event_type in EVENT_TYPES:
        assert f'"{event_type}"' in generated
