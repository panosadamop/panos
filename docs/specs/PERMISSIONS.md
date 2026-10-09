# Permissions and Approval Policy

> **Amendments (2026-10-08, ADR-005/ADR-024):** P2 defaults to *ask*. File deletion is P2 for single files recoverable from Git or a checkpoint and P3 for bulk, untracked/ignored or unrecoverable deletes. Project policy may tighten any level; loosening is limited to listed P2 classes by explicit, audited user action; P3/P4 can never be loosened by configuration. Local branch creation is P1.


## Levels
- P0 READ — automatic
- P1 SAFE_WRITE — automatic within authorized scope
- P2 CONTROLLED — configurable policy
- P3 APPROVAL_REQUIRED — explicit approval
- P4 FORBIDDEN — never execute

## Initial policy

| Action | Level |
|---|---|
| Read project files | P0 |
| Search/index repository | P0 |
| Launch approved application | P1 |
| Modify files inside active project | P1 |
| Run format/lint/unit tests | P1 |
| Install dependencies | P2 |
| Create local database/schema for dev | P2 |
| Delete project files | P2/P3 based on impact |
| Git commit | P3 |
| Git push/merge/rebase shared history | P3 |
| Docker execution | P3 |
| Kubernetes cluster mutation | P3 |
| Jira/Azure DevOps write | P3 |
| Calendar write/respond | P3 |
| Production deployment | P3 |
| Infrastructure mutation | P3 |
| Email send | P4 |
| Consequential browser submission/upload | P4 initially |

## Approval object
Approval must include action type, human-readable summary, structured payload hash, target, requesting job, expiry and user decision. If the action changes after approval, approval becomes invalid.
