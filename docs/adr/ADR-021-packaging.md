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
