"""OAuth 2.0 flows for desktop connectors (ADR-026).

* **Google:** installed-app flow with a loopback redirect (``http://127.0.0.1:<port>``),
  PKCE (S256) and a random ``state``. The browser shows Google's consent page; the
  authorization code comes back to a one-shot local listener.
* **Microsoft:** device-code flow (the user enters a short code at microsoft.com/devicelogin),
  which needs no redirect listener and works for work and personal accounts.

Token responses are turned into :class:`TokenSet` and stored only in the OS keyring.
"""

import asyncio
import base64
import hashlib
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any, Final
from urllib.parse import parse_qs, urlencode, urlsplit

from panos_core.connectors.http import ConnectorHttp, ConnectorHttpError

GOOGLE_AUTH_URL: Final = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL: Final = "https://oauth2.googleapis.com/token"  # noqa: S105 - a URL
GOOGLE_REVOKE_URL: Final = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_URL: Final = "https://openidconnect.googleapis.com/v1/userinfo"
MICROSOFT_LOGIN: Final = "https://login.microsoftonline.com"
CALLBACK_TIMEOUT_S: Final = 300.0
EXPIRY_MARGIN_S: Final = 60.0


class OAuthError(RuntimeError):
    pass


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: float  # epoch seconds
    scope: str = ""
    type: str = "oauth"

    @classmethod
    def from_response(
        cls, data: dict[str, Any], *, previous: "TokenSet | None" = None
    ) -> "TokenSet":
        if "access_token" not in data:
            raise OAuthError(f"token response without access_token: {data.get('error', '?')}")
        return cls(
            access_token=str(data["access_token"]),
            # Google omits the refresh token on refresh: keep the old one.
            refresh_token=str(
                data.get("refresh_token") or (previous.refresh_token if previous else "")
            )
            or None,
            expires_at=time.time() + float(data.get("expires_in", 3600)),
            scope=str(data.get("scope", previous.scope if previous else "")),
        )

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires_at - EXPIRY_MARGIN_S

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    return verifier, challenge.decode()


@dataclass
class LoopbackReceiver:
    """One-shot HTTP listener on 127.0.0.1 for the OAuth redirect."""

    state: str
    server: asyncio.AbstractServer | None = None
    port: int = 0
    _result: "asyncio.Future[str] | None" = None

    async def start(self) -> str:
        loop = asyncio.get_running_loop()
        self._result = loop.create_future()
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{self.port}/callback"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1")
            # Drain headers.
            while (await asyncio.wait_for(reader.readline(), 10)) not in (b"\r\n", b"\n", b""):
                pass
            parts = request_line.split(" ")
            target = parts[1] if len(parts) > 1 else "/"
            query = parse_qs(urlsplit(target).query)
            ok = False
            if urlsplit(target).path == "/callback" and self._result and not self._result.done():
                if query.get("state", [""])[0] != self.state:
                    self._result.set_exception(
                        OAuthError("state mismatch (possible CSRF); try again")
                    )
                elif "error" in query:
                    self._result.set_exception(
                        OAuthError(f"authorization denied: {query['error'][0]}")
                    )
                elif "code" in query:
                    self._result.set_result(query["code"][0])
                    ok = True
            body = (
                "Panos is connected. You can close this window."
                if ok
                else "Panos could not complete the sign-in. Return to Panos for details."
            ).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except (TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()

    async def wait(self, timeout_s: float = CALLBACK_TIMEOUT_S) -> str:
        assert self._result is not None  # noqa: S101
        try:
            return await asyncio.wait_for(asyncio.shield(self._result), timeout_s)
        except TimeoutError as exc:
            raise OAuthError("no response from the browser sign-in within 5 minutes") from exc
        finally:
            await self.close()

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None


def google_auth_url(
    client_id: str, redirect_uri: str, scopes: list[str], state: str, challenge: str
) -> str:
    return (
        GOOGLE_AUTH_URL
        + "?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",  # refresh token
                "prompt": "consent",
            }
        )
    )


async def google_exchange(
    http: ConnectorHttp,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> TokenSet:
    data = await _form(
        http,
        GOOGLE_TOKEN_URL,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )
    return TokenSet.from_response(data)


async def google_refresh(
    http: ConnectorHttp, *, client_id: str, client_secret: str, tokens: TokenSet
) -> TokenSet:
    if not tokens.refresh_token:
        raise OAuthError("no refresh token; reconnect the account")
    data = await _form(
        http,
        GOOGLE_TOKEN_URL,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": tokens.refresh_token,
            "grant_type": "refresh_token",
        },
    )
    return TokenSet.from_response(data, previous=tokens)


@dataclass(frozen=True)
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_at: float
    message: str


async def microsoft_device_code(
    http: ConnectorHttp, *, tenant: str, client_id: str, scopes: list[str]
) -> DeviceCode:
    data = await _form(
        http,
        f"{MICROSOFT_LOGIN}/{tenant}/oauth2/v2.0/devicecode",
        {"client_id": client_id, "scope": " ".join(scopes)},
    )
    return DeviceCode(
        device_code=str(data["device_code"]),
        user_code=str(data["user_code"]),
        verification_uri=str(data.get("verification_uri", "https://microsoft.com/devicelogin")),
        interval=int(data.get("interval", 5)),
        expires_at=time.time() + float(data.get("expires_in", 900)),
        message=str(data.get("message", "")),
    )


async def microsoft_poll(
    http: ConnectorHttp,
    *,
    tenant: str,
    client_id: str,
    device: DeviceCode,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> TokenSet:
    interval = device.interval
    while time.time() < device.expires_at:
        await sleep(interval)
        try:
            data = await _form(
                http,
                f"{MICROSOFT_LOGIN}/{tenant}/oauth2/v2.0/token",
                {
                    "client_id": client_id,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device.device_code,
                },
            )
        except OAuthError as exc:
            reason = str(exc)
            if "authorization_pending" in reason:
                continue
            if "slow_down" in reason:
                interval += 5
                continue
            raise
        return TokenSet.from_response(data)
    raise OAuthError("the device code expired before sign-in finished")


async def microsoft_refresh(
    http: ConnectorHttp, *, tenant: str, client_id: str, tokens: TokenSet
) -> TokenSet:
    if not tokens.refresh_token:
        raise OAuthError("no refresh token; reconnect the account")
    data = await _form(
        http,
        f"{MICROSOFT_LOGIN}/{tenant}/oauth2/v2.0/token",
        {
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
            "scope": tokens.scope,
        },
    )
    return TokenSet.from_response(data, previous=tokens)


async def _form(http: ConnectorHttp, url: str, fields: dict[str, str]) -> dict[str, Any]:
    """POST an x-www-form-urlencoded body to a token endpoint (errors become OAuthError)."""
    try:
        result = await http.request_form("POST", url, fields)
    except ConnectorHttpError as exc:
        raise OAuthError(str(exc)) from exc
    if not isinstance(result, dict):
        raise OAuthError("unexpected token endpoint response")
    return result
