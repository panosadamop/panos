# ADR-020: Audit log

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** FR-028, NFR-SEC-004, NFR-OBS-003

## Context
Consequential and security-relevant events must be recorded and resistant to silent tampering.

## Decision
- An append-only `audit_event` table. Each row stores `prev_hash` and `hash = SHA-256(prev_hash ‖ canonical(event))`.
- **Fields:** actor, node, project, job, step, tool, action, policy decision with reasons, approval ID, outcome and timestamps.
- Redaction happens before writing.
- `GET /api/v1/audit` with filters; `panos audit verify` checks the chain.
- Retention is configurable. The redacted diagnostic bundle includes an audit excerpt.

## Alternatives considered
Plain log files (not queryable, no integrity check).

## Consequences
Hash chaining detects modification by anyone without the ability to rewrite the entire chain; it is tamper-evident, not tamper-proof (acceptable for local single-user).
