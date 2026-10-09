# ADR-015: Desktop shell responsibilities (Tauri vs Python)

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** ARCHITECTURE, FR-001, FR-025, US-002, US-051

## Context
ARCHITECTURE placed `global_hotkey` and `notification` in the Python OS adapter. When the desktop runs, Tauri provides robust cross-platform implementations for both.

## Decision
- **Tauri (Rust) owns:** window, tray, global shortcut, desktop notifications, autostart, single instance, sidecar lifecycle and token handoff.
- **Python owns:** app discovery/launch, window focus/metadata, screen capture, voice and all business logic.
- Python exposes a `NotificationSink` port. Implementations: forward to the desktop over WebSocket when attached, otherwise console/log (headless).
- The WebView gets minimal Tauri capabilities: no `fs` plugin; the `shell` plugin is restricted to the sidecar.

## Alternatives considered
Electron (heavier); Python global hotkeys via pynput/keyboard (weaker cross-platform support, Wayland issues, often needs elevated access).

## Consequences
Hotkey and desktop notifications are unavailable in CLI-only mode (documented). Small Rust surface to maintain.

## Implementation notes (M7, 2026-10-09)

- **Shell:** Tauri 2 (`apps/desktop/src-tauri`). `node.rs` resolves the runtime directory exactly like `platformdirs.user_runtime_dir("Panos")` (or `PANOS_RUNTIME_DIR`), attaches to a node that answers `/api/v1/health`, and otherwise starts the sidecar and waits for `node.json`. Only a loopback host is accepted from that file.
- **Token handoff:** the WebView calls the `connection` command and keeps the base URL and token in memory only. The shell never writes the token anywhere.
- **Sidecar lookup order:** bundled `panos-node/panos-node.exe` next to the app → `PANOS_NODE_CMD` → `panos serve` on PATH (developer installs). A node the shell started is killed when the app exits; an attached node is left running.
- **Plugins:** single-instance (focuses the existing window), tray (Open Panos / Quit; closing the window hides it to the tray), autostart (off by default, toggled in Settings), global shortcut, notification. Capabilities grant only those plugins plus core window/event permissions. There is **no `fs` plugin and no `shell` plugin**; the sidecar is spawned from Rust, not from the WebView.
- **Push-to-talk:** `CommandOrControl+Shift+Space`, registered from the frontend (`desktop.ts`). Pressing it calls `POST /voice/listen`; releasing it calls `/voice/listen/stop`.
- **Desktop notifications:** the frontend shows every `notification` event from the WebSocket as a native notification. The node publishes these only for what needs the user (approvals, questions, blockers, completion, failure), never for routine progress.
- **Frontend:** React 19 + Vite. The API client is generated from the committed OpenAPI file (openapi-typescript + openapi-fetch); event types are generated from `panos_core/event_catalog.py`. CSP: `connect-src` loopback only.
