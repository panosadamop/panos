# ADR-002: Process topology: node daemon, desktop shell, CLI

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** BR-001, BR-023, NFR-REL-002, NFR-ACC-001

## Context
Long-running jobs must survive UI restarts (AC-US050-01), voice must work while the UI is minimized, and the CLI and desktop must see the same jobs and approvals.

## Decision
- The Python **Local Node** (`panos serve`) is a per-user daemon and the single owner of state, jobs, tools and voice.
- **Single instance** is enforced by a lock file in the runtime dir.
- The **desktop shell** starts the node as a Tauri sidecar if it is not running, or attaches to the running node.
- The **CLI** is a thin HTTP/WebSocket client. It auto-starts the daemon when needed.
- The node can run headless (CLI-only operation).
- Closing the desktop window does not stop the node. Quitting from the tray stops it only if the desktop started it, or if the user chooses to.

## Alternatives considered
Embedding Python in the UI process (jobs die with the UI); running core logic in-process in the CLI (duplicate job ownership, SQLite contention).

## Consequences
Node discovery requires a runtime file (port + token, ADR-003). The daemon lifecycle (autostart, crash restart) is handled by the desktop shell and OS autostart.
