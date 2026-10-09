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
