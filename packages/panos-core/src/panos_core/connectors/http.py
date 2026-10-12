"""HTTP client for connectors with an endpoint allowlist (ADR-026, BR-019).

Every request is checked against the connector's allowlist (method + host + path pattern)
**before** it is sent. Anything else (for example Gmail's ``messages.send`` or
``drafts.send``) raises :class:`EndpointNotAllowedError`, so even a bug or a manipulated
plan cannot reach a forbidden endpoint through Panos. Tokens are injected here and never
leave this module in logs or errors. 429/5xx responses are retried with backoff
(``Retry-After`` honoured); a 401 triggers one token refresh.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from panos_core.observability import get_logger

log = get_logger(__name__)

RETRY_STATUSES: Final = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES: Final = 3
MAX_RETRY_AFTER_S: Final = 30.0


@dataclass(frozen=True)
class Endpoint:
    """One allowed operation: HTTP method, host and a full-match path regex."""

    method: str
    host: str
    path: str  # regular expression, matched against the whole path

    def matches(self, method: str, host: str, path: str) -> bool:
        return (
            method.upper() == self.method.upper()
            and host.lower() == self.host.lower()
            and re.fullmatch(self.path, path) is not None
        )


class EndpointNotAllowedError(PermissionError):
    def __init__(self, method: str, url: str) -> None:
        super().__init__(f"{method} {url} is not an allowed connector endpoint")
        self.method = method
        self.url = url


class ConnectorHttpError(RuntimeError):
    """A failed connector call. ``status`` is the HTTP status (0 = transport error)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class AuthExpiredError(ConnectorHttpError):
    """The credentials were rejected and could not be refreshed: reconnect the account."""

    def __init__(self, message: str = "the connector's credentials were rejected") -> None:
        super().__init__(401, message)


# Returns the Authorization header value; ``refresh=True`` forces a token refresh.
AuthProvider = Callable[[bool], Awaitable[str]]


def _summary(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)[:300]
        if error:
            return f"{error}: {body.get('error_description', '')}"[:300]
        messages = body.get("errorMessages") or body.get("message")
        if messages:
            return str(messages)[:300]
    return str(body)[:300]


async def _read_limited(response: httpx.Response, max_bytes: int) -> bytes | None:
    if response.status_code >= 400:
        await response.aread()
        raise ConnectorHttpError(
            response.status_code, f"HTTP {response.status_code}: {_summary(response)}"
        )
    declared = response.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        return None
    data = bytearray()
    async for part in response.aiter_bytes():
        data += part
        if len(data) > max_bytes:
            return None
    return bytes(data)


class ConnectorHttp:
    def __init__(
        self,
        allowlist: Sequence[Endpoint],
        auth: AuthProvider | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._allowlist = tuple(allowlist)
        self._auth = auth
        self._client = httpx.AsyncClient(
            transport=transport, timeout=timeout_s, follow_redirects=False, trust_env=True
        )
        self._sleep = sleep

    def allowed(self, method: str, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme != "https" and parts.hostname not in ("127.0.0.1", "localhost"):
            return False
        return any(e.matches(method, parts.hostname or "", parts.path) for e in self._allowlist)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Send one request and return the decoded JSON body (``None`` for empty bodies)."""
        if not self.allowed(method, url):
            log.warning("connector_endpoint_blocked", method=method, url=url.split("?")[0])
            raise EndpointNotAllowedError(method, url.split("?")[0])
        refreshed = False
        for attempt in range(MAX_RETRIES + 1):
            hdrs = dict(headers or {})
            if self._auth is not None:
                hdrs["Authorization"] = await self._auth(False)
            try:
                response = await self._client.request(
                    method, url, params=params, json=json, data=data, headers=hdrs
                )
            except httpx.TransportError as exc:
                if attempt < MAX_RETRIES:
                    await self._sleep(2**attempt)
                    continue
                raise ConnectorHttpError(0, f"connection failed: {type(exc).__name__}") from exc
            if response.status_code == 401 and self._auth is not None and not refreshed:
                refreshed = True
                await self._auth(True)  # refresh, then retry once
                continue
            if response.status_code == 401:
                raise AuthExpiredError()
            if response.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
                delay = float(2**attempt)
                retry_after = response.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = min(MAX_RETRY_AFTER_S, float(retry_after))
                await self._sleep(delay)
                continue
            if response.status_code >= 400:
                raise ConnectorHttpError(
                    response.status_code, f"HTTP {response.status_code}: {_summary(response)}"
                )
            if not response.content:
                return None
            if "json" in response.headers.get("content-type", ""):
                return response.json()
            return response.content
        raise ConnectorHttpError(503, "gave up after retries")  # pragma: no cover

    async def download(
        self, url: str, *, max_bytes: int, params: Mapping[str, Any] | None = None
    ) -> bytes | None:
        """GET file content, at most ``max_bytes`` (``None`` when larger).

        Graph answers ``/content`` with a 302 to a short-lived, pre-authenticated URL on
        another host. That one redirect is followed **without** the Authorization header, so
        the token never reaches a host outside the allowlist.
        """
        if not self.allowed("GET", url):
            raise EndpointNotAllowedError("GET", url.split("?")[0])
        for refresh in (False, True):
            headers = {"Authorization": await self._auth(refresh)} if self._auth else {}
            async with self._client.stream("GET", url, params=params, headers=headers) as r:
                if r.status_code == 401 and self._auth is not None and not refresh:
                    continue
                if r.status_code in (301, 302, 303, 307, 308):
                    location = r.headers.get("Location", "")
                    break
                return await _read_limited(r, max_bytes)
        else:
            raise AuthExpiredError()
        if urlsplit(location).scheme != "https":
            raise ConnectorHttpError(502, "download redirected to a non-HTTPS address")
        async with self._client.stream("GET", location) as r:
            return await _read_limited(r, max_bytes)

    async def request_form(self, method: str, url: str, fields: Mapping[str, str]) -> Any:
        """A form-encoded request (OAuth token endpoints)."""
        return await self.request(method, url, data=fields)

    async def aclose(self) -> None:
        await self._client.aclose()
