"""Who is calling the hub API (ADR-028 §2, M9-07).

* **Dashboard user:** a session cookie created by a passkey sign-in (see ``passkeys``);
  unsafe methods also need the ``X-CSRF-Token`` header that matches the session.
* **Local admin:** the hub CLI on the hub PC. It sends the token from ``<hub>/admin.token``
  (readable by the owner only) and is accepted **only from loopback**.
"""

import contextlib
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Final

from fastapi import Depends, HTTPException, Request

from panos_control_plane.db.orm import HubSession, HubUser
from panos_control_plane.material import hub_dir
from panos_control_plane.state import HubState

SESSION_COOKIE: Final = "panos_hub_session"
SESSION_HOURS: Final = 12
LOOPBACK: Final = frozenset({"127.0.0.1", "::1"})
UNSAFE: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class Principal:
    kind: str  # "admin" | "user"
    name: str
    user_id: str | None = None

    @property
    def actor(self) -> str:
        return f"user:{self.name}" if self.kind == "user" else "hub-admin"


def admin_token_path(state: HubState) -> Path:
    return hub_dir(state.settings) / "admin.token"


def write_admin_token(state: HubState) -> str:
    token = secrets.token_urlsafe(32)
    path = admin_token_path(state)
    path.write_text(token, encoding="utf-8")
    with contextlib.suppress(OSError):  # Windows: the profile directory is private
        path.chmod(0o600)
    return token


def session_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def create_session(state: HubState, user_id: str) -> tuple[str, str]:
    """Returns (cookie value, CSRF token)."""
    cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    now = datetime.now(UTC)
    async with state.db() as session, session.begin():
        session.add(
            HubSession(
                id_hash=session_digest(cookie),
                user_id=user_id,
                csrf=csrf,
                created_at=now,
                expires_at=now + timedelta(hours=SESSION_HOURS),
            )
        )
    return cookie, csrf


async def session_csrf(state: HubState, cookie: str) -> str:
    async with state.db() as session:
        row = await session.get(HubSession, session_digest(cookie))
    return row.csrf if row else ""


async def end_session(state: HubState, cookie: str) -> None:
    async with state.db() as session, session.begin():
        row = await session.get(HubSession, session_digest(cookie))
        if row is not None:
            await session.delete(row)


async def principal(request: Request) -> Principal:
    state: HubState = request.app.state.hub
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        expected: str | None = request.app.state.admin_token
        client = request.client.host if request.client else ""
        if expected and client in LOOPBACK and hmac.compare_digest(header[7:], expected):
            return Principal("admin", "hub-admin")
        raise HTTPException(status_code=401, detail="invalid admin token (loopback only)")
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        raise HTTPException(status_code=401, detail="sign in with your passkey")
    async with state.db() as session:
        row = await session.get(HubSession, session_digest(cookie))
        user = await session.get(HubUser, row.user_id) if row else None
    if row is None or user is None or row.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=401, detail="the session expired; sign in again")
    if request.method in UNSAFE and not hmac.compare_digest(
        request.headers.get("X-CSRF-Token", ""), row.csrf
    ):
        raise HTTPException(status_code=403, detail="missing or wrong CSRF token")
    return Principal("user", user.name, user.id)


Caller = Annotated[Principal, Depends(principal)]
