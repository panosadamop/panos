"""``/ws/events``: live event stream (API.md, ADR-018).

Browsers cannot set an ``Authorization`` header on a WebSocket, so the client sends
``{"type": "auth", "token": "..."}`` as its first message within ``AUTH_TIMEOUT_S``;
anything else closes the socket with 1008. Host/Origin checks already ran in the
loopback middleware before the handshake was accepted.
"""

import asyncio
import contextlib
import hmac

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from panos_core.observability import get_logger
from panos_node_api.container import NodeContainer
from panos_node_api.security import AuthConfig

log = get_logger(__name__)
router = APIRouter(tags=["events"])

AUTH_TIMEOUT_S = 5.0
POLICY_VIOLATION = 1008


@router.websocket("/ws/events")
async def events(ws: WebSocket) -> None:
    await ws.accept()
    auth: AuthConfig = ws.app.state.auth
    try:
        first = await asyncio.wait_for(ws.receive_json(), AUTH_TIMEOUT_S)
    except (TimeoutError, ValueError, WebSocketDisconnect):
        log.warning("ws_auth_failed", reason="timeout_or_invalid")
        with contextlib.suppress(Exception):
            await ws.close(code=POLICY_VIOLATION)
        return
    token = first.get("token") if isinstance(first, dict) else None
    if (
        not isinstance(first, dict)
        or first.get("type") != "auth"
        or not isinstance(token, str)
        or not hmac.compare_digest(token.encode(), auth.token.encode())
    ):
        log.warning("ws_auth_failed", reason="token")
        await ws.close(code=POLICY_VIOLATION)
        return

    container: NodeContainer = ws.app.state.container
    async with container.events.subscribe() as queue:
        await ws.send_json({"type": "auth.ok", "v": 1})
        receiver = asyncio.create_task(_drain(ws))
        try:
            while not receiver.done():
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    {getter, receiver}, return_when=asyncio.FIRST_COMPLETED
                )
                if getter in done:
                    await ws.send_text(getter.result().model_dump_json())
                else:
                    getter.cancel()
        except WebSocketDisconnect:
            pass
        finally:
            receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await receiver


async def _drain(ws: WebSocket) -> None:
    """Consume (and ignore) client messages; returns when the client disconnects."""
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            await ws.receive_text()
