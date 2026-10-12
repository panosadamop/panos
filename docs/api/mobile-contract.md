# Mobile API contract (M10-08, ADR-030)

A native app (later) uses the same hub API as the PWA. The machine-readable contract is `docs/api/hub.openapi.v1.json` (versioned under `/api/v1`, regenerated and drift-checked in CI).

## Endpoints the companion uses

| Purpose | Endpoint |
|---|---|
| Sign in | `POST /auth/login/options` → WebAuthn assertion → `POST /auth/login/verify` (cookie + `csrf`) |
| First passkey | `POST /auth/register/options {code}` → attestation → `POST /auth/register/verify` |
| Session | `GET /auth/me` (name, csrf), `POST /auth/logout` |
| Badge | `GET /summary` (`pending_approvals`) |
| PCs | `GET /nodes` (status, projects) |
| Talk | `POST /converse {node, text, project?, conversation_id?}` → `{conversation_id, text, privacy, intent, provider, model, locality, sources}` |
| Jobs | `GET /jobs`, `GET /jobs/{id}` (live steps), `POST /jobs/{id}/{pause\|resume\|cancel}` |
| Approvals | `GET /approvals`, `GET /approvals/{id}` (live exact action), `POST /approvals/{id}/decision {decision}` |

## Rules

- Every request is made over HTTPS to the hub's LAN host name, and the client trusts only the hub CA. A native app should pin the CA fingerprint, like `panos node join` does.
- Unsafe methods send `X-CSRF-Token`. A native client may keep the session cookie in its own cookie store.
- Responses for local-only projects can contain live step and approval text. Never cache them on the device.
- `POST /converse` can take minutes (local models). Use a long timeout and show progress.

## Native passkeys

iOS and Android bind passkeys to a web domain through `apple-app-site-association` / `assetlinks.json` served from a public HTTPS domain. A LAN-only hub has no such domain. A native app therefore needs one of:
- a domain the owner controls, used only for the association files (the API can stay on the LAN);
- a different sign-in for the app (for example, device pairing with a one-time code and a key in the device keychain).

This decision is deferred with the native app (ADR-030 §6).
