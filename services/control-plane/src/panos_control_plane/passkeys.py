"""Passkey (WebAuthn) sign-in for the dashboard (M9-07, owner decision 2026-10-12).

* The **first** passkey is registered with a one-time ``passkey`` code created on the hub PC
  (``panos hub passkey-code``). Further passkeys can be added while signed in.
* The relying party id is the hub's host name (WebAuthn does not accept IP addresses), and
  the expected origins are ``https://<hub host names>:<port>``.
* Challenges live in memory for five minutes and are single-use.
"""

import base64
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import webauthn
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from panos_control_plane import audit, nodes
from panos_control_plane.app import PREFIX, State
from panos_control_plane.auth import (
    SESSION_COOKIE,
    SESSION_HOURS,
    create_session,
    end_session,
    principal,
    session_csrf,
)
from panos_control_plane.db.orm import HubUser, Passkey, SetupCode
from panos_control_plane.state import HubState

router = APIRouter(prefix=f"{PREFIX}/auth")
CEREMONY_S: Final = 300


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class Ceremony:
    challenge: bytes
    kind: str  # register | login
    user_id: str | None = None
    user_name: str = ""
    code: str | None = None
    label: str = ""
    created: float = field(default_factory=time.monotonic)


class Ceremonies:
    def __init__(self) -> None:
        self._items: dict[str, Ceremony] = {}

    def add(self, c: Ceremony) -> str:
        now = time.monotonic()
        self._items = {k: v for k, v in self._items.items() if now - v.created < CEREMONY_S}
        key = secrets.token_urlsafe(18)
        self._items[key] = c
        return key

    def take(self, key: str, kind: str) -> Ceremony:
        c = self._items.pop(key, None)
        if c is None or c.kind != kind or time.monotonic() - c.created >= CEREMONY_S:
            raise HTTPException(status_code=400, detail="the sign-in attempt expired; try again")
        return c


def rp_id(state: HubState) -> str:
    return (state.settings.hub.hostname or (state.material.hostnames or ["localhost"])[0]).lower()


def origins(state: HubState) -> list[str]:
    port = state.settings.hub.api_port
    hosts = dict.fromkeys([rp_id(state), *state.material.hostnames])
    suffix = "" if port == 443 else f":{port}"
    return [f"https://{h}{suffix}" for h in hosts]


def _ceremonies(request: Request) -> Ceremonies:
    store: Ceremonies | None = getattr(request.app.state, "ceremonies", None)
    if store is None:
        store = request.app.state.ceremonies = Ceremonies()
    return store


def _set_cookie(response: Response, cookie: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        cookie,
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )


class RegisterOptionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, max_length=64, description="first passkey: setup code")
    name: str = Field(default="owner", min_length=1, max_length=64)
    label: str = Field(default="", max_length=128)


class VerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ceremony: str
    credential: dict[str, Any]


class SessionOut(BaseModel):
    name: str
    csrf: str


@router.post("/register/options")
async def register_options(
    body: RegisterOptionsIn, request: Request, state: State
) -> dict[str, Any]:
    user_id, user_name = None, body.name
    if body.code is None:
        who = await principal(request)  # adding a passkey needs an existing session
        if who.user_id is None:
            raise HTTPException(status_code=403, detail="sign in first, or use a setup code")
        user_id, user_name = who.user_id, who.name
    else:
        async with state.db() as session:
            code = await session.get(SetupCode, nodes.code_hash(body.code))
        if (
            code is None
            or code.purpose != "passkey"
            or code.used_at
            or code.expires_at <= datetime.now(UTC)
        ):
            raise HTTPException(status_code=403, detail="the setup code is wrong, expired or used")
    async with state.db() as session:
        existing = (
            [
                PublicKeyCredentialDescriptor(id=unb64url(p.id))
                for p in await session.scalars(select(Passkey).where(Passkey.user_id == user_id))
            ]
            if user_id
            else []
        )
    handle = user_id or f"usr_{secrets.token_hex(12)}"
    options = webauthn.generate_registration_options(
        rp_id=rp_id(state),
        rp_name="Panos hub",
        user_id=handle.encode(),
        user_name=user_name,
        user_display_name=user_name,
        exclude_credentials=existing,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    ceremony = _ceremonies(request).add(
        Ceremony(options.challenge, "register", handle, user_name, body.code, body.label)
    )
    return {"ceremony": ceremony, "options": webauthn.options_to_json(options)}


@router.post("/register/verify", response_model=SessionOut)
async def register_verify(
    body: VerifyIn, request: Request, response: Response, state: State
) -> Any:
    c = _ceremonies(request).take(body.ceremony, "register")
    try:
        verified = webauthn.verify_registration_response(
            credential=body.credential,
            expected_challenge=c.challenge,
            expected_rp_id=rp_id(state),
            expected_origin=origins(state),
            require_user_verification=True,
        )
    except InvalidRegistrationResponse as exc:
        raise HTTPException(status_code=400, detail=f"the passkey was not accepted: {exc}") from exc
    now = datetime.now(UTC)
    assert c.user_id is not None  # noqa: S101
    async with state.db() as session, session.begin():
        if c.code is not None:
            try:
                await nodes.consume_code(session, c.code, "passkey", c.user_id)
            except nodes.NodeError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
        if await session.get(HubUser, c.user_id) is None:
            session.add(HubUser(id=c.user_id, name=c.user_name, created_at=now))
            await session.flush()
        session.add(
            Passkey(
                id=b64url(verified.credential_id),
                user_id=c.user_id,
                public_key=verified.credential_public_key,
                sign_count=verified.sign_count,
                label=c.label[:128],
                transports=list(body.credential.get("response", {}).get("transports", []) or []),
                created_at=now,
                last_used_at=now,
            )
        )
        await audit.record(
            session, f"user:{c.user_name}", "passkey.registered", details={"label": c.label}
        )
    cookie, csrf = await create_session(state, c.user_id)
    _set_cookie(response, cookie)
    return SessionOut(name=c.user_name, csrf=csrf)


@router.post("/login/options")
async def login_options(request: Request, state: State) -> dict[str, Any]:
    options = webauthn.generate_authentication_options(
        rp_id=rp_id(state), user_verification=UserVerificationRequirement.REQUIRED
    )
    ceremony = _ceremonies(request).add(Ceremony(options.challenge, "login"))
    return {"ceremony": ceremony, "options": webauthn.options_to_json(options)}


@router.post("/login/verify", response_model=SessionOut)
async def login_verify(body: VerifyIn, request: Request, response: Response, state: State) -> Any:
    c = _ceremonies(request).take(body.ceremony, "login")
    cred_id = str(body.credential.get("id", ""))
    async with state.db() as session:
        passkey = await session.get(Passkey, cred_id)
        user = await session.get(HubUser, passkey.user_id) if passkey else None
    if passkey is None or user is None:
        raise HTTPException(status_code=401, detail="unknown passkey")
    try:
        verified = webauthn.verify_authentication_response(
            credential=body.credential,
            expected_challenge=c.challenge,
            expected_rp_id=rp_id(state),
            expected_origin=origins(state),
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except InvalidAuthenticationResponse as exc:
        async with state.db() as session, session.begin():
            await audit.record(
                session, "anonymous", "signin.refused", details={"reason": str(exc)[:200]}
            )
        raise HTTPException(
            status_code=401, detail="the passkey signature was not accepted"
        ) from exc
    async with state.db() as session, session.begin():
        stored = await session.get(Passkey, cred_id)
        assert stored is not None  # noqa: S101
        stored.sign_count = verified.new_sign_count
        stored.last_used_at = datetime.now(UTC)
        await audit.record(session, f"user:{user.name}", "signin")
    cookie, csrf = await create_session(state, user.id)
    _set_cookie(response, cookie)
    return SessionOut(name=user.name, csrf=csrf)


@router.post("/logout")
async def logout(request: Request, response: Response, state: State) -> dict[str, bool]:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        await principal(request)  # CSRF applies to logout too
        await end_session(state, cookie)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=SessionOut)
async def me(request: Request, state: State) -> Any:
    who = await principal(request)
    if who.user_id is None:
        return SessionOut(name=who.name, csrf="")
    return SessionOut(
        name=who.name, csrf=await session_csrf(state, request.cookies.get(SESSION_COOKIE, ""))
    )
