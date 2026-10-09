"""Correlation-ID middleware (NFR-OBS-001).

Binds a correlation ID (client ``X-Correlation-ID`` if well-formed, else generated)
into the logging context for the request and echoes it in the response.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from panos_core.observability import bind_context, clear_context
from panos_core.observability.context import sanitize_correlation_id

HEADER = b"x-correlation-id"


class CorrelationIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        supplied = next(
            (v.decode("latin-1") for k, v in scope.get("headers", ()) if k == HEADER), None
        )
        correlation_id = sanitize_correlation_id(supplied)
        clear_context()
        bind_context(correlation_id=correlation_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((HEADER, correlation_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            clear_context()
