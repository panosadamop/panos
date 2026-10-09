# ADR-011: Job execution and durability

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** FR-022, FR-023, NFR-REL-001..004, AC-US050-01, AC-US052-01..03

## Context
Jobs run for a long time, must survive UI restarts and crashes, and must never repeat a consequential action blindly.

## Decision
- An asyncio job runner inside the node.
- Job and step records are committed **before** every consequential (P2+) step.
- **Recovery:** on startup, jobs found RUNNING, WAITING_FOR_TOOL or PLANNING become `PAUSED` with reason `interrupted`. The user (or a policy for idempotent-only jobs) must resume them.
- **Idempotency:** tools declare `idempotent`; non-idempotent steps are never auto-retried.
- **Pause** takes effect at safe points (between steps).
- **Cancel** propagates a cancellation token into running tools; processes are killed per ADR-007.

## Alternatives considered
Celery/Redis or Temporal (external services; wrong fit for a single-user local-first app).

## Consequences
One process limits parallelism to what asyncio and subprocesses provide, which is adequate for a single user.
