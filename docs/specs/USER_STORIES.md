# User Stories

> **Amendments (2026-10-08, approved plan D-11):** added US-005, US-024, US-044 and US-045 to cover BR-004, BR-027, BR-020 and BR-022, which previously had no story.

## EPIC-001 Interaction
- US-001 As a user, I can activate Panos by saying `Panos` so I can work hands-free.
- US-002 I can use a global hotkey when voice activation is inconvenient.
- US-003 I can speak Greek, English or mixed technical language.
- US-004 I can stop speech without cancelling work.
- US-005 I can mark a project or folder local-only, and Panos never sends its content or derived context to cloud AI. *(BR-004, NFR-SEC-005)*

## EPIC-002 Desktop and projects
- US-010 I can ask Panos to open an installed supported IDE/browser.
- US-011 I can register a project anywhere on disk.
- US-012 Panos analyzes an existing repository before major changes.
- US-013 I can ask Panos to inspect an error visible on my active screen.

## EPIC-003 Development
- US-020 I can request a feature in natural language and have Panos plan, implement and test it.
- US-021 I can request a specific technology stack and have it honored.
- US-022 I can ask Panos to diagnose build/test failures and attempt bounded repairs.
- US-023 I can roll back an autonomous task to its checkpoint.
- US-024 I can use voice, coding, requirements and repository analysis without internet when suitable local models are installed. *(BR-027)*

## EPIC-004 Requirements
- US-030 I can provide a brief and be interviewed for missing requirements.
- US-031 I can approve requirements hierarchically.
- US-032 I can trace a business requirement to stories, tests and implementation evidence.
- US-033 A full project generates project-specific Claude skills.

## EPIC-005 Governance
- US-040 I am asked before Git commits.
- US-041 I am asked before Docker execution.
- US-042 I am asked before Jira/Azure DevOps writes.
- US-043 Panos can draft but never send email.
- US-044 Panos shows calendar conflicts and asks me before creating, changing or responding to events. *(BR-020, FR-018)*
- US-045 Panos can research with a browser but never submits consequential forms or uploads content without me. *(BR-022, FR-016)*

## EPIC-006 Long-running work
- US-050 Panos can work autonomously while I do something else.
- US-051 Panos notifies me when approval or a business decision is needed.
- US-052 I can pause/resume/cancel a job.

## EPIC-007 Multi-device
- US-060 I can see all authorized Panos PCs and their state.
- US-061 I can dispatch an approved task to another PC.
- US-062 I can monitor tasks and approvals in a React web dashboard.
