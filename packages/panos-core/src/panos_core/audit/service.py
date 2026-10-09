"""Audit service.

Every event is redacted, then chained: ``hash = SHA-256(prev_hash ‖ canonical(event))``.
Events are written in their own transaction so they survive a failure of the
operation they describe. A process-wide lock serializes appends so the chain stays linear.
"""

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic_core import to_jsonable_python

from panos_core.canonical import canonical_bytes, sha256_hex
from panos_core.domain.enums import PolicyDecision
from panos_core.domain.models import AuditEvent
from panos_core.persistence.unit_of_work import UnitOfWorkFactory
from panos_core.redaction import redact

# Fields covered by the hash. Changing this list invalidates existing chains.
_HASHED_FIELDS = (
    "id",
    "node_id",
    "project_id",
    "job_id",
    "actor",
    "event_type",
    "action",
    "policy_decision",
    "metadata",
    "created_at",
)


def event_hash(prev_hash: str, event: AuditEvent) -> str:
    body = {name: getattr(event, name) for name in _HASHED_FIELDS}
    return sha256_hex(prev_hash.encode("ascii") + canonical_bytes(body))


class ChainVerification(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    checked: int
    first_broken_seq: int | None = None
    detail: str | None = None


class AuditService:
    def __init__(self, uow_factory: UnitOfWorkFactory, node_id: str) -> None:
        self._uow_factory = uow_factory
        self._node_id = node_id
        self._lock = asyncio.Lock()

    async def record(
        self,
        *,
        actor: str,
        event_type: str,
        action: str,
        project_id: str | None = None,
        job_id: str | None = None,
        policy_decision: PolicyDecision | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        safe_metadata = redact(to_jsonable_python(dict(metadata or {})))
        async with self._lock, self._uow_factory() as uow:
            prev = await uow.audit.last_hash()
            event = AuditEvent(
                node_id=self._node_id,
                project_id=project_id,
                job_id=job_id,
                actor=actor,
                event_type=event_type,
                action=action,
                policy_decision=policy_decision,
                metadata=safe_metadata,
                prev_hash=prev,
            )
            event.hash = event_hash(prev, event)
            await uow.audit.append(event)
            await uow.commit()
        return event

    async def list(
        self,
        *,
        project_id: str | None = None,
        job_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> Sequence[AuditEvent]:
        async with self._uow_factory() as uow:
            return await uow.audit.list(
                project_id=project_id, job_id=job_id, event_type=event_type, limit=limit
            )

    async def verify_chain(self) -> ChainVerification:
        async with self._uow_factory() as uow:
            events = await uow.audit.list_in_order()
        prev = ""
        for seq, event in events:
            if event.prev_hash != prev:
                return ChainVerification(
                    ok=False, checked=seq, first_broken_seq=seq, detail="prev_hash mismatch"
                )
            if event_hash(prev, event) != event.hash:
                return ChainVerification(
                    ok=False, checked=seq, first_broken_seq=seq, detail="content hash mismatch"
                )
            prev = event.hash
        return ChainVerification(ok=True, checked=len(events))
