"""Node registry: one-time codes, enrollment, identity checks, revocation (ADR-029).

Every operation that changes trust is audited in the hub audit chain.
"""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from cryptography import x509
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from panos_control_plane import audit
from panos_control_plane.db.orm import NodeCert, NodeRow, SetupCode
from panos_control_plane.state import HubState
from panos_core.domain.errors import DomainError
from panos_core.remote import pki

Purpose = Literal["enroll", "passkey"]
CODE_BYTES: Final = 16  # 128 bits


class NodeError(DomainError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _now() -> datetime:
    return datetime.now(UTC)


def _normalise(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def code_hash(code: str) -> str:
    return hashlib.sha256(_normalise(code).encode()).hexdigest()


def format_code(raw: bytes) -> str:
    text = base64.b32encode(raw).decode().rstrip("=")
    return "-".join(text[i : i + 4] for i in range(0, len(text), 4))


async def create_code(
    state: HubState, purpose: Purpose, *, label: str = "", actor: str = "user"
) -> tuple[str, datetime]:
    """A one-time code (shown once; only its hash is stored)."""
    code = format_code(secrets.token_bytes(CODE_BYTES))
    expires = _now() + timedelta(minutes=state.settings.hub.enroll_code_minutes)
    async with state.db() as session, session.begin():
        session.add(
            SetupCode(
                code_hash=code_hash(code),
                purpose=purpose,
                label=label[:128],
                created_at=_now(),
                expires_at=expires,
                used_at=None,
                used_by=None,
            )
        )
        await audit.record(session, actor, f"code.created.{purpose}", details={"label": label})
    return code, expires


async def consume_code(
    session: AsyncSession, code: str, purpose: Purpose, used_by: str
) -> SetupCode:
    """Mark a code used (inside the caller's transaction) or raise ``invalid_code``."""
    digest = code_hash(code)
    row = await session.scalar(
        select(SetupCode).where(SetupCode.code_hash == digest).with_for_update()
    )
    valid = (
        row is not None
        and hmac.compare_digest(row.code_hash, digest)
        and row.purpose == purpose
        and row.used_at is None
        and row.expires_at > _now()
    )
    if not valid or row is None:
        raise NodeError("invalid_code", "the code is wrong, expired or already used")
    row.used_at, row.used_by = _now(), used_by
    return row


@dataclass(frozen=True)
class Enrolled:
    node_id: str
    cert_pem: bytes
    ca_pem: bytes
    serial: str
    not_after: datetime


async def enroll(
    state: HubState,
    *,
    code: str,
    csr_pem: bytes,
    node_id: str,
    name: str,
    platform: str,
    source: str = "",
) -> Enrolled:
    if not node_id.startswith("nod_") or len(node_id) > 64:
        raise NodeError("invalid", "invalid node id")
    try:
        issued = await _enroll(state, code, csr_pem, node_id, name, platform, source)
    except NodeError as exc:
        async with state.db() as session, session.begin():
            await audit.record(
                session,
                "anonymous",
                "node.enroll_refused",
                details={"reason": exc.reason, "source": source},
            )
        raise
    return Enrolled(
        node_id, issued.cert_pem, state.material.ca_pem, issued.serial, issued.not_after
    )


async def _enroll(
    state: HubState,
    code: str,
    csr_pem: bytes,
    node_id: str,
    name: str,
    platform: str,
    source: str,
) -> pki.Issued:
    material = state.material
    async with state.db() as session, session.begin():
        used = await consume_code(session, code, "enroll", node_id)
        existing = await session.get(NodeRow, node_id)
        if existing is not None and existing.revoked_at is not None:
            raise NodeError("revoked", "this node was revoked; it must join with a new identity")
        try:
            issued = pki.sign_node_csr(material.ca_key, material.ca_cert, csr_pem, node_id)
        except pki.PkiError as exc:
            raise NodeError("invalid", str(exc)) from exc
        now = _now()
        if existing is None:
            session.add(
                NodeRow(
                    id=node_id,
                    name=(name or used.label or node_id)[:128],
                    platform=platform[:32],
                    enrolled_at=now,
                    last_seen_at=None,
                    revoked_at=None,
                )
            )
            await session.flush()
        else:  # re-join of an active node: its earlier certificates are retired
            await session.execute(
                update(NodeCert)
                .where(NodeCert.node_id == node_id, NodeCert.retired_at.is_(None))
                .values(retired_at=now)
            )
        session.add(
            NodeCert(
                serial=issued.serial,
                node_id=node_id,
                issued_at=now,
                not_after=issued.not_after,
                retired_at=None,
            )
        )
        await audit.record(
            session,
            "enrollment",
            "node.enrolled",
            node_id=node_id,
            details={"name": name, "platform": platform, "serial": issued.serial, "source": source},
        )
    return issued


async def check_peer(state: HubState, cert: x509.Certificate) -> NodeRow:
    """Authorise a verified client certificate: registered, current and not revoked."""
    try:
        node_id = pki.node_id_of(cert)
    except pki.PkiError as exc:
        raise NodeError("unauthorized", str(exc)) from exc
    serial = pki.serial_hex(cert)
    async with state.db() as session:
        row = await session.get(NodeCert, serial)
        node = await session.get(NodeRow, node_id)
    if row is None or node is None or row.node_id != node_id:
        raise NodeError("unauthorized", "unknown node certificate")
    if node.revoked_at is not None:
        raise NodeError("revoked", "the node is revoked")
    if row.retired_at is not None:
        raise NodeError("unauthorized", "the certificate was replaced")
    return node


async def revoke(state: HubState, ref: str, *, actor: str) -> NodeRow:
    async with state.db() as session, session.begin():
        node = await _find(session, ref)
        if node.revoked_at is None:
            now = _now()
            node.revoked_at = now
            await session.execute(
                update(NodeCert)
                .where(NodeCert.node_id == node.id, NodeCert.retired_at.is_(None))
                .values(retired_at=now)
            )
            await audit.record(
                session, actor, "node.revoked", node_id=node.id, details={"name": node.name}
            )
    return node


async def renew(state: HubState, node_id: str, csr_pem: bytes, old_serial: str) -> Enrolled:
    """Issue a new certificate to a connected node and retire the one it used."""
    material = state.material
    try:
        issued = pki.sign_node_csr(material.ca_key, material.ca_cert, csr_pem, node_id)
    except pki.PkiError as exc:
        raise NodeError("invalid", str(exc)) from exc
    now = _now()
    async with state.db() as session, session.begin():
        await session.execute(
            update(NodeCert).where(NodeCert.serial == old_serial).values(retired_at=now)
        )
        session.add(
            NodeCert(
                serial=issued.serial,
                node_id=node_id,
                issued_at=now,
                not_after=issued.not_after,
                retired_at=None,
            )
        )
        await audit.record(
            session,
            f"node:{node_id}",
            "node.cert_renewed",
            node_id=node_id,
            details={"serial": issued.serial, "replaced": old_serial},
        )
    return Enrolled(node_id, issued.cert_pem, material.ca_pem, issued.serial, issued.not_after)


async def _find(session: AsyncSession, ref: str) -> NodeRow:
    node = await session.get(NodeRow, ref)
    if node is None:
        matches = list(await session.scalars(select(NodeRow).where(NodeRow.name == ref)))
        if len(matches) > 1:
            raise NodeError("conflict", f"several nodes are named {ref!r}; use the node id")
        node = matches[0] if matches else None
    if node is None:
        raise NodeError("not_found", f"no node {ref!r}")
    return node


async def list_nodes(state: HubState) -> list[NodeRow]:
    async with state.db() as session:
        return list(await session.scalars(select(NodeRow).order_by(NodeRow.name)))
