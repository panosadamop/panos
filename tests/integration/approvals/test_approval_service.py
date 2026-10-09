"""Approval binding (ADR-006): exact action, single use, expiry, forgery resistance."""

from datetime import timedelta
from typing import Any

import pytest
import time_machine

from panos_core.approvals import ApprovalError, ApprovalService, action_hash
from panos_core.audit import AuditService
from panos_core.domain import ApprovalStatus
from panos_core.nodes import ensure_local_node
from panos_core.persistence import UnitOfWorkFactory

pytestmark = pytest.mark.req("FR-010", "AC-US040-02")

ACTION: dict[str, Any] = {
    "action_type": "git.commit",
    "target": "/repo",
    "payload": {"tree": "abc123", "message": "feat: x", "files": ["a.py", "b.py"]},
}


@pytest.fixture
async def svc(uow_factory: UnitOfWorkFactory) -> tuple[ApprovalService, AuditService]:
    node = await ensure_local_node(uow_factory)
    audit = AuditService(uow_factory, node.id)
    return ApprovalService(uow_factory, audit, b"n" * 32), audit


async def _approved(service: ApprovalService) -> str:
    approval = await service.request(summary="commit 2 files", actor="agent", **ACTION)
    decision = await service.decide(approval.id, approve=True, decided_by="user")
    assert decision.token is not None
    return decision.token


def test_action_hash_is_canonical() -> None:
    a = action_hash("x", "t", {"b": 1, "a": [1, 2], "n": None})
    b = action_hash("x", "t", {"n": None, "a": [1, 2], "b": 1})
    assert a == b and len(a) == 64
    assert action_hash("x", "t", {"a": [2, 1]}) != action_hash("x", "t", {"a": [1, 2]})
    assert action_hash("y", "t", {}) != action_hash("x", "t", {})


async def test_full_lifecycle_and_audit(svc: tuple[ApprovalService, AuditService]) -> None:
    service, audit = svc
    token = await _approved(service)
    consumed = await service.consume(token, actor="agent", **ACTION)
    assert consumed.status == ApprovalStatus.CONSUMED
    stored = await service.get(consumed.id)
    assert stored is not None and stored.status == ApprovalStatus.CONSUMED
    assert stored.consumed_at is not None and stored.decided_by == "user"
    types = [e.event_type for e in await audit.list(limit=10)]
    assert types[:3] == ["approval.consumed", "approval.decided", "approval.requested"]


async def test_record_has_version_decision_timestamp(
    svc: tuple[ApprovalService, AuditService],
) -> None:
    service, _ = svc
    approval = await service.request(summary="s", actor="agent", **ACTION)
    decided = (await service.decide(approval.id, approve=False, decided_by="user")).approval
    assert decided.status == ApprovalStatus.REJECTED
    assert decided.decided_at is not None and decided.payload_hash == approval.payload_hash


async def test_replay_is_rejected(svc: tuple[ApprovalService, AuditService]) -> None:
    service, audit = svc
    token = await _approved(service)
    await service.consume(token, actor="agent", **ACTION)
    with pytest.raises(ApprovalError) as err:
        await service.consume(token, actor="agent", **ACTION)
    assert err.value.reason == "replayed"
    rejected = await audit.list(event_type="approval.use_rejected")
    assert rejected[0].metadata["reason"] == "replayed"


async def test_changed_action_invalidates_approval(
    svc: tuple[ApprovalService, AuditService],
) -> None:
    service, _ = svc
    token = await _approved(service)
    changed = {**ACTION, "payload": {**ACTION["payload"], "files": ["a.py", "b.py", "c.py"]}}
    with pytest.raises(ApprovalError) as err:
        await service.consume(token, actor="agent", **changed)
    assert err.value.reason == "action_changed"
    approval_id = token.split(".")[0]
    stored = await service.get(approval_id)
    assert stored is not None and stored.status == ApprovalStatus.INVALIDATED
    # The original action can no longer use it either.
    with pytest.raises(ApprovalError):
        await service.consume(token, actor="agent", **ACTION)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t[:-1] + ("A" if t[-1] != "A" else "B"),  # flipped MAC character
        lambda t: "apr_" + "0" * 32 + "." + t.split(".")[1],  # unknown approval id
        lambda t: t.split(".")[0],  # missing MAC
        lambda t: "",  # empty
    ],
)
async def test_forged_tokens_are_rejected(
    svc: tuple[ApprovalService, AuditService], mutate: object
) -> None:
    service, _ = svc
    token = await _approved(service)
    with pytest.raises(ApprovalError) as err:
        await service.consume(mutate(token), actor="agent", **ACTION)  # type: ignore[operator]
    assert err.value.reason in ("forged", "malformed")
    # The genuine token still works afterwards.
    await service.consume(token, actor="agent", **ACTION)


async def test_token_signed_by_another_node_key_is_rejected(
    svc: tuple[ApprovalService, AuditService], uow_factory: UnitOfWorkFactory
) -> None:
    service, audit = svc
    other = ApprovalService(uow_factory, audit, b"x" * 32)
    approval = await service.request(summary="s", actor="agent", **ACTION)
    forged = (await other.decide(approval.id, approve=True, decided_by="attacker")).token
    assert forged is not None
    with pytest.raises(ApprovalError) as err:
        await service.consume(forged, actor="agent", **ACTION)
    assert err.value.reason == "forged"


async def test_unapproved_or_rejected_cannot_be_consumed(
    svc: tuple[ApprovalService, AuditService],
) -> None:
    service, _ = svc
    approval = await service.request(summary="s", actor="agent", **ACTION)
    await service.decide(approval.id, approve=False, decided_by="user")
    token = service._issue_token(approval)  # even a well-formed MAC is useless
    with pytest.raises(ApprovalError) as err:
        await service.consume(token, actor="agent", **ACTION)
    assert err.value.reason == "not_approved"


async def test_expired_approval(svc: tuple[ApprovalService, AuditService]) -> None:
    service, _ = svc
    with time_machine.travel(0, tick=False) as clock:
        approval = await service.request(
            summary="s", actor="agent", ttl=timedelta(minutes=5), **ACTION
        )
        token = (await service.decide(approval.id, approve=True, decided_by="user")).token
        assert token is not None
        clock.shift(timedelta(minutes=6))
        with pytest.raises(ApprovalError) as err:
            await service.consume(token, actor="agent", **ACTION)
        assert err.value.reason == "expired"


async def test_cannot_decide_twice_or_after_expiry(
    svc: tuple[ApprovalService, AuditService],
) -> None:
    service, _ = svc
    approval = await service.request(summary="s", actor="agent", **ACTION)
    await service.decide(approval.id, approve=True, decided_by="user")
    with pytest.raises(ApprovalError, match="APPROVED"):
        await service.decide(approval.id, approve=False, decided_by="user")
    with time_machine.travel(0, tick=False) as clock:
        late = await service.request(
            summary="late",
            actor="agent",
            ttl=timedelta(seconds=1),
            action_type="docker.exec",
            target="t",
            payload={},
        )
        clock.shift(timedelta(seconds=5))
        with pytest.raises(ApprovalError) as err:
            await service.decide(late.id, approve=True, decided_by="user")
        assert err.value.reason == "expired"
    with pytest.raises(ApprovalError) as err:
        await service.decide("apr_" + "f" * 32, approve=True, decided_by="user")
    assert err.value.reason == "not_found"


async def test_identical_pending_request_is_reused(
    svc: tuple[ApprovalService, AuditService],
) -> None:
    service, _ = svc
    first = await service.request(summary="s", actor="agent", job_id=None, **ACTION)
    second = await service.request(summary="s", actor="agent", job_id=None, **ACTION)
    assert first.id == second.id
    assert len(await service.list_pending()) == 1


def test_short_node_key_rejected(uow_factory: UnitOfWorkFactory) -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        ApprovalService(uow_factory, None, b"short")  # type: ignore[arg-type]
