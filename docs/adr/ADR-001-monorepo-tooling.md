# ADR-001: Monorepo tooling

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** NFR-MNT-001, NFR-PORT-001

## Context
Panos combines Python services, a React/TypeScript desktop UI, a Tauri (Rust) shell, a generated TypeScript client and, later, web and mobile apps. Builds must be reproducible on Windows, macOS and Linux, and architectural boundaries must be enforceable.

## Decision
- **Python:** a `uv` workspace (root `pyproject.toml`, single `uv.lock`). Members: `packages/panos-core`, `packages/panos-cli`, `services/node-api`. Python ≥ 3.12; CI tests 3.12 and 3.13.
- **TypeScript:** a `pnpm` workspace (`apps/*`, `packages/ts-client`).
- **Rust:** Cargo only inside `apps/desktop/src-tauri`.
- **Task runner:** `just` (justfile) for lint/test/generate tasks. Every recipe is a thin wrapper around a documented command, so the runner is optional.
- **Quality gates:** ruff (lint + format), mypy `--strict`, import-linter, pytest; ESLint, Prettier, `tsc --noEmit`, Vitest; `cargo clippy`/`cargo test`.
- **CI:** GitHub Actions matrix over windows-latest, macos-latest and ubuntu-latest.
- **Layout:** see `docs/specs/REPOSITORY_STRUCTURE.md`.

## Alternatives considered
Poetry (slower, weaker workspace support); Nx/Turborepo (unnecessary overhead for a two-language repo of this size); Bazel (excessive).

## Consequences
Developers need uv, pnpm, Rust and just installed. The single lockfile per ecosystem keeps dependency audits simple. import-linter contracts make the boundary rules in REPOSITORY_STRUCTURE.md fail the build when violated.
