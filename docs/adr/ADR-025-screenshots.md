# ADR-025: Screenshot handling

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** BR-021, FR-015, NFR-PRV-002, US-013, AC-US013-01..04

## Context
A screenshot may show anything, including content from local-only projects or other applications.

## Decision
- Capture only on explicit request or an approved workflow step.
- Images are kept in memory only, labeled `LOCAL_ONLY` by default, and analyzed by a local vision model.
- Cloud vision only with explicit per-request consent, and only when the active project policy permits it.
- Never persisted unless the user saves the image or diagnostics retention is enabled.
- Every capture is audited (metadata only, no image).

## Alternatives considered
Persisting screenshots for history (privacy risk).

## Consequences
Analysis quality depends on available local vision models on CPU-only hardware (see S-1 / ADR-009).
