# ADR-003: Local API security

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** NFR-SEC-001, API.md (Security), SECURITY trust boundary 1

## Context
Any local process or web page could try to reach a loopback HTTP server. Browsers enable DNS-rebinding attacks against localhost services.

## Decision
- Bind only to `127.0.0.1` on a random free port.
- **Runtime file:** port and a random 256-bit bearer token are written to a runtime file readable only by the current user (POSIX `0600`; on Windows, an ACL granting access to the owner only).
- **Required on every request:** `Authorization: Bearer <token>`; `Host` ∈ {`127.0.0.1:<port>`, `localhost:<port>`}; `Origin` absent or in the allowlist (the Tauri origin).
- **WebSocket:** the client must send an auth message first, within 5 s, or the socket is closed.
- No CORS wildcard and no arbitrary shell or exec endpoint.
- The token rotates on every node start.

## Alternatives considered
Unix domain sockets / named pipes only (the WebView cannot use them directly; different on each OS); no authentication (unsafe).

## Consequences
The Tauri shell reads the runtime file and passes the token to the WebView through a Tauri command, never through URL parameters. Tests cover 401/400/403 paths.
