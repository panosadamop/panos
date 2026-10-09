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

## Implementation notes (M5, 2026-10-09)

- **Runner** (`panos_core/jobs/runner.py`): asyncio tasks, at most `jobs.max_concurrent` (default 2). Workflows are registered by kind: `coding`, `command`.
- **Persisted progress:** `job.kind/input/state/result/status_reason` (migration 0005). Workflows record each step *before* it runs (`JobService.start_step`, NFR-REL-001). The coding workflow records completed phases, so resume continues after the last completed phase. An interrupted implement phase is rolled back to its checkpoint first, so edits are never applied twice.
- **Recovery:** at startup, RUNNING/PLANNING/WAITING_FOR_TOOL jobs become PAUSED (`interrupted`) and QUEUED jobs start. Verified with a hard kill of the real node process. On Windows the job's tool processes die with the node (Job Object kill-on-close).
- **Pause** is honoured at safe points (between phases, edits and commands). **Cancel** fires the cancel token (process trees killed) and ends the job CANCELLED with a summary.
- **Approvals:** a tool needing approval suspends the job (WAITING_FOR_APPROVAL) with the pending call in its state. The decision resumes the job through an `ApprovalService` listener, and the re-run consumes the token for the identical call. Tokens are kept in memory only. A WAITING_FOR_APPROVAL job can be resumed to re-issue an expired request.
- **Limits** (FR-023, `jobs.*` settings, per-job `budget`): active runtime (waiting time excluded), AI tokens and cost (from `ai_usage` by job id), and repair attempts. A limit hit means BLOCKED with a report. A workflow report with `success: false` is never recorded as COMPLETED (NFR-REL-004).
