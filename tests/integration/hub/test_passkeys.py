"""Dashboard passkey sign-in, sessions and CSRF (M9-07, NFR-SEC-006, AC-US062-02)."""

import ssl

import httpx
import pytest

from panos_control_plane import nodes
from panos_control_plane.server import RunningHub
from tests.webauthn_soft import SoftAuthenticator

pytestmark = pytest.mark.req("NFR-SEC-006", "US-062")


def browser(running: RunningHub) -> httpx.AsyncClient:
    ctx = ssl.create_default_context(cadata=running.state.material.ca_pem.decode())
    return httpx.AsyncClient(base_url=f"https://localhost:{running.api_port}/api/v1", verify=ctx)


async def register(c: httpx.AsyncClient, auth: SoftAuthenticator, code: str) -> httpx.Response:
    opts = (
        await c.post(
            "/auth/register/options", json={"code": code, "name": "panos", "label": "laptop"}
        )
    ).json()
    return await c.post(
        "/auth/register/verify",
        json={"ceremony": opts["ceremony"], "credential": auth.create(opts["options"])},
    )


async def test_first_passkey_needs_a_one_time_code_then_signs_in(running: RunningHub) -> None:
    origin = f"https://localhost:{running.api_port}"
    auth = SoftAuthenticator(origin)
    async with browser(running) as c:
        bad = await c.post("/auth/register/options", json={"code": "WRONG-CODE-1234"})
        assert bad.status_code == 403
        code, _ = await nodes.create_code(running.state, "passkey")
        done = await register(c, auth, code)
        assert done.status_code == 200, done.text
        cookie = done.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=strict" in cookie.lower()
        csrf = done.json()["csrf"]

        assert (await c.get("/nodes")).status_code == 200  # signed in
        no_csrf = await c.post("/codes", json={"purpose": "enroll"})
        assert no_csrf.status_code == 403  # unsafe method without the CSRF header
        ok = await c.post("/codes", json={"purpose": "enroll"}, headers={"X-CSRF-Token": csrf})
        assert ok.status_code == 200 and ok.json()["code"]
        passkey_code = await c.post(
            "/codes", json={"purpose": "passkey"}, headers={"X-CSRF-Token": csrf}
        )
        assert passkey_code.status_code == 403  # only on the hub PC
        assert (await c.post("/auth/logout", headers={"X-CSRF-Token": csrf})).status_code == 200
        assert (await c.get("/nodes")).status_code == 401

        reused = await c.post("/auth/register/options", json={"code": code})
        assert reused.status_code == 403  # the code was single-use

        opts = (await c.post("/auth/login/options")).json()
        signed = await c.post(
            "/auth/login/verify",
            json={
                "ceremony": opts["ceremony"],
                "credential": auth.get(opts["options"], "localhost"),
            },
        )
        assert signed.status_code == 200 and signed.json()["name"] == "panos"
        me = (await c.get("/auth/me")).json()
        assert me["name"] == "panos" and me["csrf"] == signed.json()["csrf"]

        # Replaying the same ceremony fails (challenges are single-use).
        replay = await c.post(
            "/auth/login/verify",
            json={
                "ceremony": opts["ceremony"],
                "credential": auth.get(opts["options"], "localhost"),
            },
        )
        assert replay.status_code == 400

    events = [r["event_type"] for r in await _audit(running)]
    assert {"passkey.registered", "signin"} <= set(events)


async def test_wrong_origin_or_key_is_refused(running: RunningHub) -> None:
    origin = f"https://localhost:{running.api_port}"
    auth = SoftAuthenticator(origin)
    async with browser(running) as c:
        code, _ = await nodes.create_code(running.state, "passkey")
        opts = (await c.post("/auth/register/options", json={"code": code})).json()
        phished = auth.create(opts["options"], origin="https://evil.example")
        bad = await c.post(
            "/auth/register/verify", json={"ceremony": opts["ceremony"], "credential": phished}
        )
        assert bad.status_code == 400
        assert (await register(c, auth, code)).status_code == 200
        await c.post(
            "/auth/logout", headers={"X-CSRF-Token": (await c.get("/auth/me")).json()["csrf"]}
        )

        opts = (await c.post("/auth/login/options")).json()
        impostor = SoftAuthenticator(
            origin, credential_id=auth.credential_id, user_handle=auth.user_handle
        )
        forged = impostor.get(opts["options"], "localhost")  # same id, different private key
        r = await c.post(
            "/auth/login/verify", json={"ceremony": opts["ceremony"], "credential": forged}
        )
        assert r.status_code == 401


async def _audit(running: RunningHub) -> list[dict[str, str]]:
    from sqlalchemy import select

    from panos_control_plane.db.orm import HubAudit

    async with running.state.db() as s:
        return [{"event_type": r.event_type} for r in await s.scalars(select(HubAudit))]
