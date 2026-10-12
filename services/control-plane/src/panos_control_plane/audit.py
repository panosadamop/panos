"""Hub audit log, hash-chained like the node audit (ADR-020, ADR-028 §8).

``hash = SHA-256(prev_hash ‖ canonical(event))``. Writers serialise on a PostgreSQL
transaction-level advisory lock, so concurrent events cannot fork the chain.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from panos_control_plane.db.orm import HubAudit
from panos_core.canonical import canonical_bytes, sha256_hex
from panos_core.redaction import redact

GENESIS: Final = "0" * 64
_LOCK_KEY: Final = 0x50414E4F53  # "PANOS"


def _hash(prev: str, row: HubAudit) -> str:
    body = {
        "created_at": row.created_at.isoformat(),
        "actor": row.actor,
        "event_type": row.event_type,
        "node_id": row.node_id,
        "details": row.details,
    }
    return sha256_hex(prev.encode("ascii") + canonical_bytes(body))


async def record(
    session: AsyncSession,
    actor: str,
    event_type: str,
    *,
    node_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> HubAudit:
    """Append one event inside the caller's transaction (commit it with the change)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
    last = await session.scalar(select(HubAudit.hash).order_by(HubAudit.seq.desc()).limit(1))
    row = HubAudit(
        created_at=datetime.now(UTC),
        actor=actor[:200],
        event_type=event_type,
        node_id=node_id,
        details=redact(details or {}),
        prev_hash=last or GENESIS,
    )
    row.hash = _hash(row.prev_hash, row)
    session.add(row)
    await session.flush()
    return row


@dataclass(frozen=True)
class ChainCheck:
    ok: bool
    checked: int
    first_broken_seq: int | None = None


async def verify(session: AsyncSession) -> ChainCheck:
    prev = GENESIS
    checked = 0
    for row in await session.scalars(select(HubAudit).order_by(HubAudit.seq)):
        checked += 1
        if row.prev_hash != prev or _hash(prev, row) != row.hash:
            return ChainCheck(False, checked, row.seq)
        prev = row.hash
    return ChainCheck(True, checked)
