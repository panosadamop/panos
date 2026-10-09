"""Approval service (ADR-006).

* The approved action is identified by ``action_hash`` = SHA-256 of the RFC 8785
  canonical form of ``{action_type, target, payload}``.
* Approving issues a token ``<approval_id>.<mac>`` where
  ``mac = HMAC-SHA256(node_key, approval_id | action_hash | expires_at)``.
* Consuming re-hashes the *current* action, verifies the MAC in constant time and
  atomically flips APPROVED → CONSUMED. Any mismatch, expiry or reuse fails closed
  and is audited. If the action changed after approval, the approval is INVALIDATED.
"""

import base64
import hashlib
import hmac
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any, NoReturn

from pydantic import BaseModel, ConfigDict

from panos_core.audit import AuditService
from panos_core.canonical import canonical_hash
from panos_core.domain.clock import utc_now
from panos_core.domain.enums import ApprovalStatus
from panos_core.domain.errors import DomainError
from panos_core.domain.models import ActionApproval
from panos_core.persistence.unit_of_work import UnitOfWorkFactory

DEFAULT_TTL = timedelta(minutes=30)


def action_hash(action_type: str, target: str, payload: Mapping[str, Any]) -> str:
    return canonical_hash({"action_type": action_type, "target": target, "payload": payload})


class ApprovalError(DomainError):
    """An approval could not be decided or used. ``reason`` is a stable code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval: ActionApproval
    token: str | None


class ApprovalService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        audit: AuditService,
        node_key: bytes,
        *,
        default_ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        if len(node_key) < 32:
            raise ValueError("node key must be at least 32 bytes")
        self._uow_factory = uow_factory
        self._audit = audit
        self._key = node_key
        self._ttl = default_ttl

    # ------------------------------------------------------------------ request
    async def request(
        self,
        *,
        action_type: str,
        target: str,
        payload: Mapping[str, Any],
        summary: str,
        actor: str,
        job_id: str | None = None,
        project_id: str | None = None,
        ttl: timedelta | None = None,
    ) -> ActionApproval:
        """Create (or reuse an identical pending) approval request."""
        digest = action_hash(action_type, target, payload)
        now = utc_now()
        async with self._uow_factory() as uow:
            existing = await uow.approvals.find_pending(job_id, digest)
            if existing is not None and existing.expires_at > now:
                return existing
            approval = ActionApproval(
                project_id=project_id,
                job_id=job_id,
                action_type=action_type,
                target=target,
                payload_hash=digest,
                summary=summary,
                requested_at=now,
                expires_at=now + (ttl or self._ttl),
            )
            await uow.approvals.add(approval)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="approval.requested",
            action=action_type,
            project_id=project_id,
            job_id=job_id,
            metadata={
                "approval_id": approval.id,
                "target": target,
                "summary": summary,
                "payload_hash": digest,
                "expires_at": approval.expires_at,
            },
        )
        return approval

    # ------------------------------------------------------------------ decide
    async def decide(
        self, approval_id: str, *, approve: bool, decided_by: str, comment: str = ""
    ) -> ApprovalDecision:
        now = utc_now()
        async with self._uow_factory() as uow:
            approval = await uow.approvals.get(approval_id)
            if approval is None:
                raise ApprovalError("not_found", f"approval {approval_id!r} not found")
            if approval.status != ApprovalStatus.PENDING:
                raise ApprovalError("not_pending", f"approval is {approval.status}")
            if approval.expires_at <= now:
                approval.status = ApprovalStatus.EXPIRED
                await uow.approvals.update(approval)
                await uow.commit()
                raise ApprovalError("expired", "approval request has expired")
            approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
            approval.decided_by = decided_by
            approval.decided_at = now
            await uow.approvals.update(approval)
            await uow.commit()
        await self._audit.record(
            actor=decided_by,
            event_type="approval.decided",
            action=approval.action_type,
            project_id=approval.project_id,
            job_id=approval.job_id,
            metadata={"approval_id": approval.id, "decision": approval.status, "comment": comment},
        )
        return ApprovalDecision(
            approval=approval, token=self._issue_token(approval) if approve else None
        )

    # ------------------------------------------------------------------ consume
    async def consume(
        self,
        token: str,
        *,
        action_type: str,
        target: str,
        payload: Mapping[str, Any],
        actor: str,
    ) -> ActionApproval:
        """Verify ``token`` against the action about to execute and mark it consumed."""
        approval_id, _, mac = token.partition(".")
        if not approval_id or not mac:
            await self._reject(actor, None, "malformed", action_type)
        async with self._uow_factory() as uow:
            approval = await uow.approvals.get(approval_id)
        if approval is None:
            await self._reject(actor, None, "forged", action_type)
        if not hmac.compare_digest(mac, self._mac(approval)):
            await self._reject(actor, approval, "forged", action_type)

        digest = action_hash(action_type, target, payload)
        now = utc_now()
        if digest != approval.payload_hash:
            if approval.status == ApprovalStatus.APPROVED:
                await self._set_status(approval, ApprovalStatus.INVALIDATED)
            await self._reject(actor, approval, "action_changed", action_type)
        if approval.status == ApprovalStatus.CONSUMED:
            await self._reject(actor, approval, "replayed", action_type)
        if approval.status != ApprovalStatus.APPROVED:
            await self._reject(actor, approval, "not_approved", action_type)
        if approval.expires_at <= now:
            await self._set_status(approval, ApprovalStatus.EXPIRED)
            await self._reject(actor, approval, "expired", action_type)

        async with self._uow_factory() as uow:
            consumed = await uow.approvals.consume(approval.id, digest, now)
            await uow.commit()
        if not consumed:  # lost a race with another consumer
            await self._reject(actor, approval, "replayed", action_type)
        approval.status = ApprovalStatus.CONSUMED
        approval.consumed_at = now
        await self._audit.record(
            actor=actor,
            event_type="approval.consumed",
            action=action_type,
            project_id=approval.project_id,
            job_id=approval.job_id,
            metadata={"approval_id": approval.id},
        )
        return approval

    async def list_pending(self) -> Sequence[ActionApproval]:
        async with self._uow_factory() as uow:
            return await uow.approvals.list_by_status(ApprovalStatus.PENDING)

    async def get(self, approval_id: str) -> ActionApproval | None:
        async with self._uow_factory() as uow:
            return await uow.approvals.get(approval_id)

    # ------------------------------------------------------------------ helpers
    def _mac(self, approval: ActionApproval) -> str:
        message = f"{approval.id}|{approval.payload_hash}|{approval.expires_at.isoformat()}"
        digest = hmac.new(self._key, message.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def _issue_token(self, approval: ActionApproval) -> str:
        return f"{approval.id}.{self._mac(approval)}"

    async def _set_status(self, approval: ActionApproval, status: ApprovalStatus) -> None:
        async with self._uow_factory() as uow:
            current = await uow.approvals.get(approval.id)
            if current is not None and current.status == ApprovalStatus.APPROVED:
                current.status = status
                await uow.approvals.update(current)
                await uow.commit()
        approval.status = status

    async def _reject(
        self, actor: str, approval: ActionApproval | None, reason: str, action_type: str
    ) -> NoReturn:
        await self._audit.record(
            actor=actor,
            event_type="approval.use_rejected",
            action=action_type,
            project_id=approval.project_id if approval else None,
            job_id=approval.job_id if approval else None,
            metadata={"approval_id": approval.id if approval else None, "reason": reason},
        )
        raise ApprovalError(reason, f"approval cannot be used: {reason}")
