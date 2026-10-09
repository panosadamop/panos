# Claude Code Implementation Plan

> **Amendments (2026-10-08, approved plan):** the detailed, traced backlog is in `docs/planning/PLAN_PROPOSAL.md` §5. Changes to this outline:
> - **M0 (new)** pre-work: ADRs, spec amendments, traceability registry, S-1 voice/local-LLM spike.
> - **M2** also delivers the core job state machine (moved forward from M5) because approvals require `WAITING_FOR_APPROVAL`.
> - **M3** also delivers basic lexical local knowledge indexing; **M4** adds vector/hybrid retrieval (MVP item 17 was otherwise unscheduled).
> - **M8** also includes browser research (FR-016) and Jira/Azure DevOps read/prepare/approval-gated writes (BR-016), which were previously unscheduled.
> - Windows is the first production OS adapter (ADR-022).


## Milestone 1 — Skeleton
- Create Python package and FastAPI service.
- Create domain entities for Project, Job, JobStep, Approval, AuditEvent.
- Configure SQLite and migrations.
- Implement structured logging/correlation IDs.
- Add unit-test infrastructure.

Exit: `/health` works and core domain tests pass.

## Milestone 2 — Policy and tools
- Tool interface/registry.
- Permission policy engine.
- Approval service with payload hashing/expiry.
- Filesystem read/write tools scoped to projects.
- Shell tool with cancellation/timeouts.
- Audit integration.

Exit: forbidden/approval-required actions cannot bypass policy.

## Milestone 3 — Projects and repository intelligence
- workspace/project registry;
- stack/build/test detection;
- repository snapshot/map;
- Git read tools;
- app discovery/launch adapters.

Exit: CLI can register/analyze/open a real project.

## Milestone 4 — AI layer
- provider interfaces;
- local/cloud provider configuration;
- model capability registry;
- router with local-only hard boundary and budgets;
- prompt/context builder with untrusted-content separation.

Exit: tests prove local-only content cannot route to cloud.

## Milestone 5 — Orchestration/coding
- job state machine;
- planner and developer/reviewer roles;
- checkpoint manager;
- plan → edit → test → review flow;
- bounded repair loops;
- Git commit approval flow;
- Docker approval flow.

Exit: fixture repository feature can be implemented end-to-end without auto-commit.

## Milestone 6 — Requirements
- brief/interview workflow;
- requirements entities/versioning;
- hierarchical approvals;
- Markdown artifact generation;
- trace links;
- initial estimation;
- Claude skills generator.

Exit: brief can become approved spec/backlog and generated project skeleton.

## Milestone 7 — Desktop and voice
- Tauri/React desktop;
- conversation/task/approval UI;
- WebSocket events;
- global hotkey;
- wake-word/STT/TTS provider interfaces and initial implementations;
- desktop notifications;
- active-window capture/analyze.

Exit: user can operate MVP primarily through desktop/voice.

## Milestone 8 — Integrations
- email read/search/summarize/draft with no send capability;
- calendar read + approval-gated writes;
- knowledge ingestion adapters;
- document exports.

## Milestone 9 — Distributed system
- control plane;
- node enrollment and mTLS identity;
- remote dispatch;
- React dashboard.

## Milestone 10 — Mobile and optimization
- mobile companion;
- estimation feedback;
- model/cost analytics;
- advanced integrations.

## Implementation rule
Do not jump ahead to visually impressive UI or remote automation while policy, privacy, job state, tool validation and audit foundations are incomplete.
