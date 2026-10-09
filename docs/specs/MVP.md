# MVP Scope

> **Amendments (2026-10-08, ADR-022):** the first production-ready OS adapter is Windows. MVP = milestones M0–M7 of the approved plan.


The MVP is a safe, useful single-PC developer/requirements assistant. Do not begin multi-PC/mobile/control-plane work until this foundation is stable.

## MVP capabilities
1. Windows/macOS/Linux core abstraction with at least one production-ready OS adapter first.
2. CLI and local FastAPI runtime.
3. React/Tauri desktop shell.
4. Project registry and `~/Panos/Projects` workspace.
5. Application discovery/launch for supported IDEs/browsers.
6. Local project/repository analysis.
7. Filesystem and controlled shell tools.
8. Git read operations and approval-gated commit.
9. Approval-gated Docker execution.
10. Provider-neutral local/cloud AI routing with local-only enforcement.
11. Requirements interview and Markdown artifact generation.
12. Coding agent: plan/edit/test/review workflow.
13. Job state machine, cancellation, limits and audit log.
14. Checkpoints/rollback for project modifications.
15. Voice interface: wake word, STT, TTS, hotkey.
16. Active-window screenshot analysis on explicit command.
17. Basic local knowledge indexing/RAG.
18. Claude skill generation for created projects.

## Explicitly later
- remote multi-PC dispatch
- web control plane/dashboard beyond local status
- mobile app
- full Jira/Azure DevOps writes
- broad cloud-drive knowledge integrations
- advanced estimation learning
