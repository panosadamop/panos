# ADR-021: Packaging and distribution

- **Status:** Accepted (details finalized in M7)
- **Date:** 2026-10-08
- **Traces:** MVP#3, BR-002

## Context
The desktop app must ship with the Python node without requiring a system Python.

## Decision
- The Python node is frozen with **PyInstaller (onedir)** and bundled as a Tauri sidecar.
- **Tauri bundler targets:**
  - Windows: MSI/NSIS (first target);
  - macOS: dmg, signed and notarized when distributed;
  - Linux: AppImage/deb.
- Voice and embedding models are downloaded on first use with checksum verification, not bundled.
- Since usage is personal/internal (D-6), code signing is optional initially.

## Alternatives considered
Nuitka (slower builds); requiring a system Python with uv (simpler, but a worse install experience).

## Consequences
Native extensions (onnxruntime, ctranslate2) must be validated in the frozen build in M7.

## Implementation notes (M7, 2026-10-09)

- **Sidecar:** `scripts/build_sidecar.py [--voice]` runs PyInstaller one-folder into `apps/desktop/src-tauri/sidecar/panos-node/`. That output is git-ignored.
  - Size: 315 MB with voice (onnxruntime, ctranslate2, Piper, sounddevice).
  - Bundled data: package data, Alembic env and migrations (loaded from disk as source), and hidden imports (`aiosqlite`, `keyring.backends.Windows`, `win32timezone`).
  - The frozen executable runs the voice worker as `panos-node voice-worker <config>`.
- **Installer:** `pnpm tauri build --config src-tauri/tauri.bundle.conf.json` (NSIS, per-user install) copies the sidecar folder in as a resource. The plain `tauri.conf.json` has no resources, so development builds don't need the sidecar. The installer is 113 MB.
- **Smoke test (frozen build, isolated directories):**
  - `version` returns in 1–4 s;
  - `serve` reaches healthy in ~7 s, including migrations 0001–0006;
  - project creation and a chat turn work;
  - the frozen voice worker loads the real models and reaches `idle` in ~21 s;
  - clean shutdown.
  - Two frozen-only defects were found this way and fixed: the Alembic `env.py` was missing, and the SQLAlchemy driver `aiosqlite` was not detected.
- **Models:** not bundled. `panos voice setup` downloads them.
- **Not done:**
  - code signing;
  - checksum verification of model downloads;
  - macOS/Linux bundles (the CI `shell` job only builds and tests the Rust crate);
  - an automated installer test. The installer has been built but not run.
