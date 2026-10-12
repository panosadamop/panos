"""Node links: the hub end of the mTLS WebSocket (ADR-028 §2–4, ADR-029 §4–5).

* TLS requires a client certificate from the hub CA. The node identity then comes from that
  verified certificate and is authorised against the registry (``nodes.check_peer``).
* Frames are decoded with the allowlisted protocol; repeated invalid frames close the link.
* Mirrors are written with the privacy filter of ADR-028 §7: step summaries and approval
  summaries of LOCAL_ONLY projects are never persisted.
* A sweep re-checks connected nodes, so a revocation made by another process (the CLI)
  still closes the link within seconds.
"""

import asyncio
import contextlib
import json
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from cryptography import x509
from sqlalchemy.dialects.postgresql import insert
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from panos_control_plane import audit, nodes
from panos_control_plane.db.orm import ApprovalMirror, DispatchRow, JobMirror, NodeCert, NodeRow
from panos_control_plane.state import HubState
from panos_core.observability import get_logger
from panos_core.remote import pki
from panos_core.remote.protocol import (
    MAX_FRAME_BYTES,
    ApprovalUpdate,
    Heartbeat,
    Hello,
    Inventory,
    JobUpdate,
    ProtocolError,
    Renew,
    Reply,
    decode,
    encode,
)

log = get_logger(__name__)

MAX_PROTOCOL_ERRORS: Final = 5
CLOSE_UNAUTHORIZED: Final = 4401
CLOSE_REVOKED: Final = 4403
CLOSE_REPLACED: Final = 4409
CLOSE_PROTOCOL: Final = 4400
LOCAL_ONLY: Final = "LOCAL_ONLY"


def _now() -> datetime:
    return datetime.now(UTC)


class LinkError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason  # offline | timeout | refused


@dataclass
class NodeLink:
    node_id: str
    serial: str
    ws: ServerConnection
    pending: dict[str, asyncio.Future[Reply]] = field(default_factory=dict)

    async def send(self, frame: str) -> None:
        await self.ws.send(frame)


Listener = Callable[[str, str, Any], Awaitable[None]]  # (node_id, event, message)


class LinkManager:
    def __init__(self, state: HubState) -> None:
        self._state = state
        self._links: dict[str, NodeLink] = {}
        self._server: Server | None = None
        self._sweeper: asyncio.Task[None] | None = None
        self._listeners: list[Listener] = []

    # ------------------------------------------------------------------ lifecycle
    def ssl_context(self) -> ssl.SSLContext:
        m = self._state.material
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(m.server_cert, m.server_key, password=m.passphrase)
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cadata=m.ca_pem.decode())
        return ctx

    async def start(self, host: str, port: int, *, sweep_s: float = 5.0) -> int:
        self._server = await serve(
            self._handle,
            host,
            port,
            ssl=self.ssl_context(),
            max_size=MAX_FRAME_BYTES,
            server_header=None,
        )
        self._sweeper = asyncio.create_task(self._sweep(sweep_s))
        sock = next(iter(self._server.sockets))
        return int(sock.getsockname()[1])

    async def close(self) -> None:
        if self._sweeper:
            self._sweeper.cancel()
        for link in list(self._links.values()):
            await link.ws.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    # ------------------------------------------------------------------ queries
    def online(self, node_id: str) -> bool:
        return node_id in self._links

    def online_ids(self) -> set[str]:
        return set(self._links)

    # ------------------------------------------------------------------ connection
    async def _handle(self, ws: ServerConnection) -> None:
        transport = getattr(ws, "transport", None)
        ssl_object = transport.get_extra_info("ssl_object") if transport else None
        der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
        if not der:
            await ws.close(CLOSE_UNAUTHORIZED, "client certificate required")
            return
        cert = x509.load_der_x509_certificate(der)
        try:
            node = await nodes.check_peer(self._state, cert)
        except nodes.NodeError as exc:
            log.warning("node_link_refused", reason=exc.reason)
            await ws.close(
                CLOSE_REVOKED if exc.reason == "revoked" else CLOSE_UNAUTHORIZED, str(exc)
            )
            return
        previous = self._links.get(node.id)
        if previous is not None:
            await previous.ws.close(CLOSE_REPLACED, "replaced by a newer connection")
        link = NodeLink(node.id, pki.serial_hex(cert), ws)
        self._links[node.id] = link
        await self._touch(node.id)
        log.info("node_link_open", node_id=node.id)
        errors = 0
        try:
            async for frame in ws:
                try:
                    env, msg = decode(frame, "to_hub")
                except ProtocolError as exc:
                    errors += 1
                    log.warning("node_link_protocol_error", node_id=node.id, error=str(exc))
                    if errors >= MAX_PROTOCOL_ERRORS:
                        await ws.close(CLOSE_PROTOCOL, "too many invalid frames")
                        break
                    continue
                await self._on_message(link, env.id, msg)
        except ConnectionClosed:
            pass
        finally:
            if self._links.get(node.id) is link:
                del self._links[node.id]
            for future in link.pending.values():
                if not future.done():
                    future.set_exception(LinkError("offline", "the node disconnected"))
            await self._notify(node.id, "disconnected", None)
            log.info("node_link_closed", node_id=node.id)

    async def _on_message(self, link: NodeLink, msg_id: str, msg: Any) -> None:
        if isinstance(msg, Reply):
            future = link.pending.pop(msg.to, None)
            if future is not None and not future.done():
                future.set_result(msg)
            return
        if isinstance(msg, Hello):
            if msg.node_id != link.node_id:  # the certificate, not the message, is the identity
                await link.ws.close(CLOSE_UNAUTHORIZED, "node id does not match the certificate")
                return
            await self._update_node(
                link.node_id,
                platform=msg.platform,
                version=msg.version,
                tools=msg.tools,
                features=msg.features,
                accepts_remote_approvals=msg.accepts_remote_approvals,
            )
            await link.send(encode(Reply(to=msg_id, ok=True)))
        elif isinstance(msg, Heartbeat):
            await self._update_node(link.node_id, running_jobs=msg.running_jobs)
        elif isinstance(msg, Inventory):
            await self._update_node(link.node_id, projects=[p.model_dump() for p in msg.projects])
        elif isinstance(msg, JobUpdate):
            await self._mirror_job(link.node_id, msg)
        elif isinstance(msg, ApprovalUpdate):
            await self._mirror_approval(link.node_id, msg)
        elif isinstance(msg, Renew):
            await self._renew(link, msg_id, msg)
        await self._notify(link.node_id, type(msg).__name__.lower(), msg)

    async def _notify(self, node_id: str, event: str, msg: Any) -> None:
        for listener in self._listeners:
            try:
                await listener(node_id, event, msg)
            except Exception:  # a listener must not break the link
                log.warning("link_listener_failed", link_event=event, exc_info=True)

    # ------------------------------------------------------------------ persistence
    async def _touch(self, node_id: str) -> None:
        await self._update_node(node_id)

    async def _update_node(self, node_id: str, **values: Any) -> None:
        async with self._state.db() as session, session.begin():
            node = await session.get(NodeRow, node_id)
            if node is None:
                return
            node.last_seen_at = _now()
            for key, value in values.items():
                setattr(node, key, value)

    async def _mirror_job(self, node_id: str, msg: JobUpdate) -> None:
        local = msg.privacy_mode == LOCAL_ONLY
        steps = [
            {**s.model_dump(mode="json"), "summary": None if local else s.summary}
            for s in msg.steps
        ]
        values = {
            "node_id": node_id,
            "kind": msg.kind,
            "objective": msg.objective,
            "status": msg.status,
            "status_reason": None if local else msg.status_reason,
            "phase": msg.phase,
            "project_id": msg.project_id,
            "project_name": msg.project_name,
            "privacy_mode": msg.privacy_mode,
            "dispatch_id": msg.dispatch_id,
            "created_at": msg.created_at,
            "completed_at": msg.completed_at,
            "updated_at": _now(),
            "steps": steps,
        }
        async with self._state.db() as session, session.begin():
            existing = await session.get(JobMirror, msg.job_id)
            if existing is not None and existing.node_id != node_id:
                log.warning("job_mirror_conflict", job_id=msg.job_id, node_id=node_id)
                return  # a node can only report its own jobs
            stmt = insert(JobMirror).values(job_id=msg.job_id, **values)
            await session.execute(
                stmt.on_conflict_do_update(index_elements=[JobMirror.job_id], set_=values)
            )
            if msg.dispatch_id:
                dispatch = await session.get(DispatchRow, msg.dispatch_id)
                if dispatch is not None and dispatch.node_id == node_id and dispatch.job_id is None:
                    dispatch.job_id = msg.job_id

    async def _mirror_approval(self, node_id: str, msg: ApprovalUpdate) -> None:
        values = {
            "node_id": node_id,
            "job_id": msg.job_id,
            "project_id": msg.project_id,
            "action_type": msg.action_type,
            "target": msg.target,
            "payload_hash": msg.payload_hash,
            "summary": None if msg.privacy_mode == LOCAL_ONLY else msg.summary,
            "status": msg.status,
            "privacy_mode": msg.privacy_mode,
            "requested_at": msg.requested_at,
            "expires_at": msg.expires_at,
            "updated_at": _now(),
        }
        async with self._state.db() as session, session.begin():
            existing = await session.get(ApprovalMirror, msg.approval_id)
            if existing is not None and existing.node_id != node_id:
                return
            stmt = insert(ApprovalMirror).values(approval_id=msg.approval_id, **values)
            await session.execute(
                stmt.on_conflict_do_update(index_elements=[ApprovalMirror.approval_id], set_=values)
            )

    async def _renew(self, link: NodeLink, msg_id: str, msg: Renew) -> None:
        try:
            done = await nodes.renew(self._state, link.node_id, msg.csr.encode(), link.serial)
        except nodes.NodeError as exc:
            await link.send(encode(Reply(to=msg_id, ok=False, error=str(exc))))
            return
        link.serial = done.serial
        await link.send(
            encode(
                Reply(
                    to=msg_id,
                    ok=True,
                    data={
                        "certificate": done.cert_pem.decode(),
                        "not_after": done.not_after.isoformat(),
                    },
                )
            )
        )

    # ------------------------------------------------------------------ requests to nodes
    async def request(self, node_id: str, message: Any, *, wait_s: float = 30.0) -> Reply:
        link = self._links.get(node_id)
        if link is None:
            raise LinkError("offline", f"node {node_id} is not connected")
        frame = encode(message)
        msg_id = decode_id(frame)
        future: asyncio.Future[Reply] = asyncio.get_running_loop().create_future()
        link.pending[msg_id] = future
        try:
            await link.send(frame)
            return await asyncio.wait_for(future, wait_s)
        except TimeoutError as exc:
            raise LinkError("timeout", f"node {node_id} did not answer in {wait_s:.0f}s") from exc
        finally:
            link.pending.pop(msg_id, None)

    async def disconnect(
        self, node_id: str, *, code: int = CLOSE_REVOKED, reason: str = ""
    ) -> None:
        link = self._links.pop(node_id, None)
        if link is not None:
            await link.ws.close(code, reason or "disconnected by the hub")

    async def _sweep(self, every: float) -> None:
        while True:
            await asyncio.sleep(every)
            for node_id, link in list(self._links.items()):
                with contextlib.suppress(Exception):
                    await self._recheck(node_id, link)

    async def _recheck(self, node_id: str, link: NodeLink) -> None:
        async with self._state.db() as session:
            node = await session.get(NodeRow, node_id)
            cert = await session.get(NodeCert, link.serial)
        if node is None or node.revoked_at is not None or cert is None or cert.retired_at:
            async with self._state.db() as session, session.begin():
                await audit.record(
                    session,
                    "hub",
                    "node.link_closed",
                    node_id=node_id,
                    details={"reason": "revoked or certificate retired"},
                )
            await self.disconnect(node_id, reason="revoked")


def decode_id(frame: str) -> str:
    return str(json.loads(frame)["id"])
