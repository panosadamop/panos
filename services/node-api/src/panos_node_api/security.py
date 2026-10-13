"""Loopback API protection (ADR-003).

A pure ASGI middleware (so it also covers WebSocket handshakes) that enforces, for
every HTTP request and WebSocket connection:

* ``Host`` header is one of the configured loopback host:port values (DNS-rebinding defense);
* ``Origin`` header, when present, is in the allowlist;
* HTTP requests carry ``Authorization: Bearer <token>`` (constant-time comparison).

CORS: the desktop WebView's origin differs from the node's, so browsers send a preflight
(``OPTIONS`` without credentials) before each authenticated request. A preflight from an
allowlisted origin is answered without the token; it grants nothing by itself. Responses
to allowlisted origins echo that exact origin (never a wildcard).

WebSocket token authentication happens in the first message (added with ``/ws/events``).
"""

import hmac
from collections.abc import Iterable
from dataclasses import dataclass, field

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from panos_core.observability import get_logger
from panos_node_api.problems import PROBLEM_CONTENT_TYPE, problem_bytes

log = get_logger(__name__)


@dataclass(frozen=True)
class AuthConfig:
    token: str
    allowed_hosts: frozenset[str]
    allowed_origins: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def for_loopback(cls, token: str, port: int, origins: Iterable[str] = ()) -> "AuthConfig":
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        return cls(token=token, allowed_hosts=frozenset(hosts), allowed_origins=frozenset(origins))


_CORS_METHODS = b"GET, POST, PUT, PATCH, DELETE"
_CORS_HEADERS = b"authorization, content-type, x-correlation-id"
_CORS_MAX_AGE = b"600"


def _cors_headers(origin: str) -> list[tuple[bytes, bytes]]:
    return [
        (b"access-control-allow-origin", origin.encode("latin-1")),
        (b"access-control-expose-headers", b"x-correlation-id"),
        (b"vary", b"Origin"),
    ]


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", ()):
        if key == name:
            decoded: str = value.decode("latin-1")
            return decoded
    return None


class LoopbackSecurityMiddleware:
    def __init__(self, app: ASGIApp, config: AuthConfig) -> None:
        self.app = app
        self.config = config
        self._expected = f"Bearer {config.token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        host = _header(scope, b"host")
        if host is None or host.lower() not in self.config.allowed_hosts:
            await self._reject(scope, send, 400, "invalid host header", reason="host")
            return

        origin = _header(scope, b"origin")
        if origin is not None and origin not in self.config.allowed_origins:
            await self._reject(scope, send, 403, "origin not allowed", reason="origin")
            return

        if (
            scope["type"] == "http"
            and origin is not None
            and scope.get("method") == "OPTIONS"
            and _header(scope, b"access-control-request-method") is not None
        ):
            await self._preflight(send, origin)
            return

        if origin is not None and scope["type"] == "http":
            send = _with_cors(send, origin)

        if scope["type"] == "http":
            supplied = (_header(scope, b"authorization") or "").encode("latin-1")
            if not hmac.compare_digest(supplied, self._expected):
                await self._reject(
                    scope, send, 401, "missing or invalid bearer token", reason="token"
                )
                return

        await self.app(scope, receive, send)

    async def _preflight(self, send: Send, origin: str) -> None:
        headers = [
            *_cors_headers(origin),
            (b"access-control-allow-methods", _CORS_METHODS),
            (b"access-control-allow-headers", _CORS_HEADERS),
            (b"access-control-max-age", _CORS_MAX_AGE),
            (b"content-length", b"0"),
        ]
        await send({"type": "http.response.start", "status": 204, "headers": headers})
        await send({"type": "http.response.body", "body": b""})

    async def _reject(
        self, scope: Scope, send: Send, status: int, detail: str, *, reason: str
    ) -> None:
        log.warning(
            "api_request_rejected",
            reason=reason,
            status=status,
            path=scope.get("path"),
            transport=scope["type"],
        )
        if scope["type"] == "websocket":
            close: Message = {"type": "websocket.close", "code": 1008}
            await send(close)
            return
        body = problem_bytes(status, detail)
        headers = [
            (b"content-type", PROBLEM_CONTENT_TYPE.encode()),
            (b"content-length", str(len(body)).encode()),
        ]
        if status == 401:
            headers.append((b"www-authenticate", b"Bearer"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def _with_cors(send: Send, origin: str) -> Send:
    """Add CORS headers for an allowlisted origin to the response start message."""

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = [*message.get("headers", ()), *_cors_headers(origin)]
            message = {**message, "headers": headers}
        await send(message)

    return wrapped
