"""The built dashboard in real Chromium: passkey setup and sign-in with a virtual authenticator
(M9-07/08, US-062). Skipped when the dashboard is not built or Chromium is unavailable."""

import base64
import hashlib
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization

from panos_control_plane import nodes
from panos_control_plane.dashboard import dist_dir
from panos_control_plane.server import RunningHub
from panos_core.remote import pki

pytestmark = pytest.mark.req("BR-025", "FR-027", "US-062", "AC-US062-01", "NFR-SEC-006")


def spki_pin(running: RunningHub) -> str:
    cert = pki.load_cert(running.state.material.server_cert.read_bytes())
    der = cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(hashlib.sha256(der).digest()).decode()


async def test_passkey_setup_and_sign_in_in_chromium(running: RunningHub, tmp_path: Any) -> None:
    if dist_dir() is None:
        pytest.skip("dashboard not built (pnpm --dir apps/dashboard build)")
    playwright = pytest.importorskip("playwright.async_api")
    async with playwright.async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                args=[f"--ignore-certificate-errors-spki-list={spki_pin(running)}"]
            )
        except Exception as exc:  # Chromium not installed
            pytest.skip(f"Chromium unavailable: {exc}")
        page = await browser.new_page()
        cdp = await page.context.new_cdp_session(page)
        await cdp.send("WebAuthn.enable")
        await cdp.send(
            "WebAuthn.addVirtualAuthenticator",
            {
                "options": {
                    "protocol": "ctap2",
                    "transport": "internal",
                    "hasResidentKey": True,
                    "hasUserVerification": True,
                    "isUserVerified": True,
                }
            },
        )
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        url = f"https://localhost:{running.api_port}/"
        await page.goto(url)
        await page.get_by_role("button", name="Σύνδεση με passkey").wait_for()

        code, _ = await nodes.create_code(running.state, "passkey")
        await page.get_by_label("Κωδικός ρύθμισης (από `panos hub passkey-code`)").fill(code)
        await page.get_by_label("Το όνομά σου").fill("panos")
        await page.get_by_role("button", name="Ρύθμιση passkey").last.click()
        await page.get_by_role("heading", name="Επισκόπηση").wait_for(timeout=15000)

        await page.get_by_role("button", name="Αποσύνδεση").click()
        await page.get_by_role("button", name="Σύνδεση με passkey").click()
        await page.get_by_role("heading", name="Επισκόπηση").wait_for(timeout=15000)
        await page.get_by_role("button", name="Υπολογιστές").click()
        await page.get_by_role("heading", name="Υπολογιστές").wait_for()
        csp_violations = [e for e in errors if "Content Security Policy" in e]
        assert csp_violations == []
        await browser.close()


@pytest.mark.req("BR-026")
async def test_companion_pwa_on_a_phone_sized_screen(running: RunningHub) -> None:
    if dist_dir() is None:
        pytest.skip("dashboard not built (pnpm --dir apps/dashboard build)")
    playwright = pytest.importorskip("playwright.async_api")
    async with playwright.async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                args=[f"--ignore-certificate-errors-spki-list={spki_pin(running)}"]
            )
        except Exception as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        context = await browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="en-US"
        )
        page = await context.new_page()
        cdp = await context.new_cdp_session(page)
        await cdp.send("WebAuthn.enable")
        await cdp.send(
            "WebAuthn.addVirtualAuthenticator",
            {
                "options": {
                    "protocol": "ctap2",
                    "transport": "internal",
                    "hasResidentKey": True,
                    "hasUserVerification": True,
                    "isUserVerified": True,
                }
            },
        )
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        base = f"https://localhost:{running.api_port}"
        await page.goto(f"{base}/?app=companion")
        manifest = await page.evaluate(
            "fetch('/manifest.webmanifest').then(async r => [r.headers.get('content-type'),"
            " (await r.json()).start_url])"
        )
        assert manifest[0].startswith("application/manifest+json")
        assert manifest[1] == "/?app=companion"
        code, _ = await nodes.create_code(running.state, "passkey")
        await page.locator("#setup-code").fill(code)
        await page.locator("#setup-name").fill("phone")
        await page.locator("form button[type=submit]").click()
        await page.locator("#talk-title").wait_for(timeout=15000)  # the companion opens on Talk
        registered = await page.evaluate(
            "navigator.serviceWorker.ready.then(r => !!r.active).catch(() => false)"
        )
        assert registered is True
        await page.reload()
        controlled = await page.evaluate("!!navigator.serviceWorker.controller")
        assert controlled is True
        width = await page.evaluate("document.documentElement.scrollWidth")
        assert width <= 390 + 1  # no horizontal page scroll on the phone
        assert [e for e in errors if "Content Security Policy" in e] == []
        await browser.close()
