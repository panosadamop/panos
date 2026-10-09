# Functional Requirements

> **Amendments (2026-10-08, ADR-014):** FR-010 approval states are unified with REQUIREMENTS_PIPELINE.md: `DRAFT`, `PENDING_APPROVAL`, `APPROVED`, `REJECTED`, `SUPERSEDED`, plus an orthogonal `stale` flag. Tool-action approvals use their own states (ADR-006).


## FR-001 Activation
- Detect `Panos` locally without streaming continuous ambient audio to cloud services.
- Support configurable global hotkey and CLI activation.
- Support wake-word sensitivity and temporary disable/mute.

## FR-002 Conversation
- Accept text and speech.
- Automatically detect Greek/English/mixed speech.
- Return text responses and, when voice is enabled, TTS responses.
- `stop talking` stops TTS without cancelling the task.
- `stop`, `pause`, `resume` control task execution.

## FR-003 Application registry
- Discover supported installed applications.
- Maintain aliases and executable/application identifiers.
- Launch JetBrains products, VS Code, Sublime and installed browsers.
- Allow configurable preferred IDE per project/technology.

## FR-004 Project registry
- Default workspace `~/Panos/Projects`.
- Register arbitrary external project paths.
- Store project privacy policy, preferred IDE, AI policy, repository metadata and knowledge settings.

## FR-005 Repository onboarding
Produce a repository model covering stack, file/module map, dependencies, build/test commands, architecture, coding conventions, Git state/history and known documentation before major modifications.

## FR-006 Coding workflow
For non-trivial changes: understand → analyze → plan → checkpoint → implement → format/lint/type-check/build/test → review → traceability check → report.

## FR-007 Project creation
Given a natural-language brief, Panos shall:
1. capture the brief;
2. identify ambiguity;
3. interview the user;
4. produce requirements;
5. propose architecture/stack when unspecified;
6. request approval;
7. create repository and documentation;
8. generate Claude skills;
9. implement incrementally;
10. test/review;
11. open in preferred IDE.

## FR-008 Requirements artifacts
Support BRD, FRD, NFR, actors, use cases, business rules, validations, errors, Epics, Features, Stories, acceptance criteria, APIs/OpenAPI, data model/ERD, architecture, test cases, estimates, sprint plan, roadmap, risks, assumptions and dependencies.

## FR-009 Requirements traceability
Maintain links from business requirement to functional requirement, backlog item, acceptance criterion, API/data elements, test cases and implementation evidence.

## FR-010 Approval model
Support item and hierarchical approvals with approved/rejected/pending/superseded states, approver, timestamp, version and comments.

## FR-011 Tool execution
AI output shall request typed tools. Tool runtime validates schema, policy, permissions, context and target before execution.

## FR-012 Terminal
Support PowerShell, shell/zsh/bash and configured WSL environments. Capture stdout/stderr, exit code, duration and command metadata.

## FR-013 Git
Support status/diff/log/branch/repository analysis. Require approval for commit, push, merge, shared-history rewrite, releases/tags and destructive actions.

## FR-014 Docker/Kubernetes
Inspect configuration freely. Require approval before Docker execution. Kubernetes cluster-changing operations are consequential and require approval.

## FR-015 Screen analysis
Capture active window/screen only on explicit user request or approved workflow. Analyze UI/errors and avoid persistent screenshot storage by default.

## FR-016 Browser research
Open/navigate/read approved pages and conduct research. Do not autonomously submit consequential forms or upload content.

## FR-017 Email
Support Microsoft 365/Outlook, Gmail and Thunderbird where integration capabilities permit. Read/search/summarize/draft only. Sending is forbidden.

## FR-018 Calendar
Read schedule and free/busy; identify conflicts. Prepare create/update/delete/respond operations and execute only after approval.

## FR-019 Knowledge ingestion
Ingest supported folders/files, Git repositories, Word/PDF/Excel/PowerPoint/Markdown, email, SharePoint, OneDrive, Google Drive and selected websites through provider adapters.

## FR-020 Knowledge retrieval
Rank current project first, then company/past approved/user knowledge, approved external sources and general model knowledge. Preserve provenance.

## FR-021 AI routing
Select model based on privacy boundary, offline state, capability, context, complexity, latency and configured cost budget.

## FR-022 Long-running jobs
Persist jobs and steps with QUEUED, PLANNING, RUNNING, WAITING_FOR_TOOL, WAITING_FOR_APPROVAL, WAITING_FOR_USER, PAUSED, BLOCKED, FAILED, COMPLETED and CANCELLED states.

## FR-023 Limits
Jobs support maximum runtime, cost, token usage and autonomous repair attempts.

## FR-024 Checkpoints
Create recoverable checkpoints before substantial autonomous project modifications and allow task-level rollback.

## FR-025 Notifications
Use desktop + optional voice notifications for required decisions, approvals, blockers and completion. Avoid continuous narration.

## FR-026 Multi-node
Register authorized Panos nodes, expose capabilities/status and securely dispatch permitted jobs to selected nodes.

## FR-027 Dashboard
React dashboard exposes nodes, projects, jobs, approvals, logs, AI usage/cost and traceability.

## FR-028 Audit
Record security-relevant and consequential events including actor, node, project, tool, action, policy decision, approval, outcome and timestamps.

## FR-029 Document generation
Generate Markdown plus export pipelines for DOCX, XLSX, PPTX and PDF.

## FR-030 Claude skills
Generate project skills only from approved project facts/standards. Regenerate or version skills when approved architecture/standards change.
