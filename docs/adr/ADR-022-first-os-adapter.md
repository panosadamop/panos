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
