"""The node end of the hub link (ADR-028 §3–6).

The node dials out to the hub over mTLS and keeps one WebSocket open. It announces
itself (``hello``, ``inventory``, a snapshot of current jobs and approvals), sends
heartbeats, publishes job and approval changes, and answers the hub's allowlisted
requests through a :class:`RemoteHandler`. Each request is decided by this node's own
services and policy. A revoked node stops reconnecting.
"""

import asyncio
import contextlib
import random
from collections.abc import Awaitable, Callable
from typing import Any, Final, Literal, Protocol
from urllib.parse import urlsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from panos_core.config import PanosSettings
from panos_core.observability import get_logger
from panos_core.remote import membership, pki
from panos_core.remote.protocol import (
    MAX_FRAME_BYTES,
    ApprovalUpdate,
    Control,
    Decision,
    Dispatch,
    Heartbeat,
    Hello,
    Inventory,
    JobUpdate,
    ProtocolError,
    Query,
    Renew,
    Reply,
    decode,
    encode,
)
from panos_core.secrets import SecretStore

log = get_logger(__name__)

LinkState = Literal["disabled", "connecting", "connected", "disconnected", "revoked"]
REVOKED_CODES: Final = frozenset({4401, 4403})


class RemoteRefusalError(Exception):
    """The node refuses a hub request (reason is sent back to the hub)."""


class RemoteHandler(Protocol):
    async def hello(self) -> Hello: ...
    async def inventory(self) -> Inventory: ...
    async def snapshot(self) -> list[JobUpdate | ApprovalUpdate]: ...
    async def running_jobs(self) -> int: ...
    async def dispatch(self, msg: Dispatch) -> dict[str, Any]: ...
    async def control(self, msg: Control) -> dict[str, Any]: ...
    async def decision(self, msg: Decision) -> dict[str, Any]: ...
    async def query(self, msg: Query) -> Any: ...


Connector = Callable[..., Awaitable[ClientConnection]]


class HubLink:
    def __init__(
        self,
        settings: PanosSettings,
        secret_store: SecretStore,
        handler: RemoteHandler,
        *,
        connector: Connector | None = None,
    ) -> None:
        self._settings = settings
        self._secrets = secret_store
        self._handler = handler
        self._connect = connector or connect
        self._task: asyncio.Task[None] | None = None
        self._ws: ClientConnection | None = None
        self._outbox: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self._pending: dict[str, asyncio.Future[Reply]] = {}
        self._answers: set[asyncio.Task[None]] = set()
        self.state: LinkState = "disabled"
        self.last_error: str | None = None
        self.connected = asyncio.Event()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> bool:
        """Start linking if this node has joined a hub and linking is enabled."""
        if not self._settings.remote.enabled or membership.load(self._settings) is None:
            self.state = "disabled"
            return False
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="hub-link")
        return True

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._ws is not None:
            await self._ws.close()
        self.state = "disabled"

    def publish(self, message: JobUpdate | ApprovalUpdate | Inventory) -> None:
        """Queue a change for the hub (dropped while offline: reconnect sends a snapshot)."""
        if self.state != "connected":
            return
        with contextlib.suppress(asyncio.QueueFull):
            self._outbox.put_nowait(encode(message))

    # ------------------------------------------------------------------ connection loop
    async def _run(self) -> None:
        delay = 1.0
        while True:
            self.state = "connecting"
            try:
                await self._session()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                code = exc.rcvd.code if exc.rcvd else None
                if code in REVOKED_CODES:
                    self.state = "revoked"
                    self.last_error = exc.rcvd.reason if exc.rcvd else "revoked"
                    log.warning("hub_link_revoked", reason=self.last_error)
                    return
                self.last_error = f"connection closed ({code})"
            except (OSError, InvalidStatus, membership.MembershipError, TimeoutError) as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
            self.connected.clear()
            self.state = "disconnected"
            log.info("hub_link_retry", in_s=round(delay, 1), error=self.last_error)
            await asyncio.sleep(delay * (0.8 + 0.4 * random.random()))  # noqa: S311 - jitter
            delay = min(delay * 2, self._settings.remote.reconnect_max_s)

    async def _session(self) -> None:
        info = membership.load(self._settings)
        if info is None:
            raise membership.MembershipError("not_joined", "no hub membership")
        ca, cert, key = membership.credentials(self._settings, self._secrets)
        ctx = pki.client_context(ca, cert, key, scratch=self._settings.runtime_dir)
        host = urlsplit(info.link_url).hostname
        async with await self._open(info.link_url, ctx, host) as ws:
            self._ws = ws
            receiver = asyncio.create_task(self._receive(ws))
            try:
                hello = await self._request(self._handler_hello(), ws)
                if not hello.ok:
                    raise ConnectionClosed(None, None)
                await ws.send(encode(await self._handler.inventory()))
                for item in await self._handler.snapshot():
                    await ws.send(encode(item))
                self.state = "connected"
                self.last_error = None
                self.connected.set()
                log.info("hub_link_connected", hub=info.hub_url)
                await self._maybe_renew(ws, cert)
                sender = asyncio.create_task(self._send_loop(ws))
                beat = asyncio.create_task(self._heartbeat(ws))
                try:
                    await receiver
                finally:
                    sender.cancel()
                    beat.cancel()
            finally:
                receiver.cancel()
                self._ws = None
                for future in self._pending.values():
                    if not future.done():
                        future.cancel()
                self._pending.clear()

    async def _open(self, url: str, ctx: Any, host: str | None) -> ClientConnection:
        return await self._connect(
            url, ssl=ctx, server_hostname=host, max_size=MAX_FRAME_BYTES, open_timeout=15
        )

    def _handler_hello(self) -> Awaitable[Hello]:
        return self._handler.hello()

    async def _request(self, message: Awaitable[Any], ws: ClientConnection) -> Reply:
        msg = await message
        frame = encode(msg)
        msg_id = frame.split('"id":"', 1)[1].split('"', 1)[0]
        future: asyncio.Future[Reply] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        await ws.send(frame)
        return await asyncio.wait_for(future, 30)

    async def _send_loop(self, ws: ClientConnection) -> None:
        while True:
            await ws.send(await self._outbox.get())

    async def _heartbeat(self, ws: ClientConnection) -> None:
        while True:
            await asyncio.sleep(self._settings.remote.heartbeat_s)
            await ws.send(encode(Heartbeat(running_jobs=await self._handler.running_jobs())))

    async def _maybe_renew(self, ws: ClientConnection, cert_pem: bytes) -> None:
        if not pki.needs_renewal(pki.load_cert(cert_pem)):
            return
        info = membership.load(self._settings)
        assert info is not None  # noqa: S101
        key = pki.generate_key()

        async def renew_msg() -> Renew:
            return Renew(csr=pki.make_csr(key, info.node_id).decode())

        reply = await self._request(renew_msg(), ws)
        if reply.ok and isinstance(reply.data, dict):
            membership.store_certificate(
                self._settings,
                reply.data["certificate"].encode(),
                pki.key_to_pem(key),
                self._secrets,
            )
            log.info("hub_link_certificate_renewed")

    # ------------------------------------------------------------------ incoming
    async def _receive(self, ws: ClientConnection) -> None:
        async for frame in ws:
            try:
                env, msg = decode(frame, "to_node")
            except ProtocolError as exc:
                log.warning("hub_link_protocol_error", error=str(exc))
                continue
            if isinstance(msg, Reply):
                future = self._pending.pop(msg.to, None)
                if future is not None and not future.done():
                    future.set_result(msg)
                continue
            task = asyncio.create_task(self._answer(ws, env.id, msg))
            self._answers.add(task)
            task.add_done_callback(self._answers.discard)

    async def _answer(self, ws: ClientConnection, msg_id: str, msg: Any) -> None:
        try:
            if isinstance(msg, Dispatch):
                data: Any = await self._handler.dispatch(msg)
            elif isinstance(msg, Control):
                data = await self._handler.control(msg)
            elif isinstance(msg, Decision):
                data = await self._handler.decision(msg)
            elif isinstance(msg, Query):
                data = await self._handler.query(msg)
            else:  # pragma: no cover - the protocol allowlist already rejects others
                raise RemoteRefusalError("unsupported request")
            reply = Reply(to=msg_id, ok=True, data=data)
        except RemoteRefusalError as exc:
            reply = Reply(to=msg_id, ok=False, error=str(exc)[:2000])
        except Exception as exc:
            log.warning("hub_request_failed", request=type(msg).__name__, exc_info=True)
            reply = Reply(to=msg_id, ok=False, error=f"{type(exc).__name__}: {exc}"[:2000])
        with contextlib.suppress(ConnectionClosed):
            await ws.send(encode(reply))
