# ADR-022: Windows as first production OS adapter

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** MVP#1, BR-002, NFR-PORT-002

## Context
The MVP requires at least one production-ready OS adapter. The development and reference machine is Windows 11 (decision D-2).

## Decision
- **Windows** is the first production-ready adapter.
- **macOS and Linux** adapters implement discovery, launch and `open_path` from M3. Window metadata and capture follow in M7.
- Unsupported capabilities return a typed `Unsupported` result, never crash.
- The same contract test suite runs against every adapter in the CI OS matrix.

## Alternatives considered
Building all three to production level at once (slower MVP).

## Consequences
macOS/Linux users get degraded capabilities until post-MVP hardening; this is documented in the capability endpoint.

## Implementation notes (M3, 2026-10-09)

- **Desktop adapter contract:** `capabilities()`, `discover_apps()`, `launch()`, `open_path()`, `active_window()`, `focus_window()`. Unsupported operations raise `UnsupportedError`. A contract test runs against the real adapter of each OS.
- **Windows (production):** discovery from uninstall registry keys, App Paths and install directories (where JetBrains Toolbox installs IDEs). Browsers prefer App Paths, because their uninstall entries point into version folders that move on update. Launch is detached `CreateProcess` with an argv list, outside any tool job object. Active-window metadata and focus use Win32 APIs.
- **macOS:** `/Applications` bundles, launch via `open -n -a`. **Linux:** PATH, Toolbox scripts, snap and flatpak exports; launch with `xdg-open`. Active window and focus are reported unsupported until M7.
- `open_path` refuses executables (by extension, plus the POSIX exec bit), because opening them with the default handler would run them.
