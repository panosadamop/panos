"""Audit hash chain (FR-028, ADR-020)."""

import sqlite3
from itertools import pairwise

import pytest

from panos_core.audit import AuditService
from panos_core.config import PanosSettings
from panos_core.domain import PolicyDecision
from panos_core.nodes import ensure_local_node
from panos_core.persistence import UnitOfWorkFactory

pytestmark = pytest.mark.req("FR-028")


@pytest.fixture
async def audit(uow_factory: UnitOfWorkFactory) -> AuditService:
    node = await ensure_local_node(uow_factory)
    return AuditService(uow_factory, node.id)


async def _fill(audit: AuditService, n: int = 5) -> None:
    for i in range(n):
        await audit.record(
            actor="agent",
            event_type="policy.decision",
            action=f"a{i}",
            policy_decision=PolicyDecision.ALLOW,
            metadata={"i": i, "f": 0.5},
        )


async def test_events_are_chained_and_verify(audit: AuditService) -> None:
    await _fill(audit)
    events = list(reversed(await audit.list(limit=10)))
    assert events[0].prev_hash == ""
    for prev, cur in pairwise(events):
        assert cur.prev_hash == prev.hash
    result = await audit.verify_chain()
    assert result.ok and result.checked == 5


@pytest.mark.req("NFR-SEC-004")
async def test_metadata_is_redacted_before_hashing(audit: AuditService) -> None:
    event = await audit.record(
        actor="agent",
        event_type="x",
        action="y",
        metadata={"api_key": "sk-ant-" + "z" * 30, "note": "ok"},
    )
    assert event.metadata == {"api_key": "[REDACTED]", "note": "ok"}
    assert (await audit.verify_chain()).ok


@pytest.mark.parametrize(
    ("sql", "detail"),
    [
        ("UPDATE audit_event SET action='tampered' WHERE seq=3", "content hash mismatch"),
        ("UPDATE audit_event SET metadata_json='{\"i\": 99}' WHERE seq=2", "content hash mismatch"),
        ("DELETE FROM audit_event WHERE seq=3", "prev_hash mismatch"),
    ],
)
async def test_tampering_is_detected(
    audit: AuditService, settings: PanosSettings, sql: str, detail: str
) -> None:
    await _fill(audit)
    with sqlite3.connect(settings.database_path) as conn:
        conn.execute(sql)
    result = await audit.verify_chain()
    assert not result.ok
    assert result.detail == detail
    assert result.first_broken_seq in (2, 3, 4)


async def test_concurrent_appends_keep_a_linear_chain(audit: AuditService) -> None:
    import asyncio

    await asyncio.gather(
        *(audit.record(actor="a", event_type="t", action=str(i)) for i in range(20))
    )
    assert (await audit.verify_chain()).ok
