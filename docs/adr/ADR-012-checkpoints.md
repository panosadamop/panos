# ADR-012: Checkpoints via shadow Git repository

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** FR-024, US-023, AC-US020-02, AC-US023-01..03

## Context
Rollback must work for Git and non-Git projects without touching the user's history, index or stash.

## Decision
- Each project gets a **shadow Git repository** under the Panos data dir (`<data>/checkpoints/<project_id>.git`), operated with `GIT_DIR=<shadow>` and `GIT_WORK_TREE=<project>`.
- The project's `.gitignore` is honored, plus Panos excludes (`.git/`, large binaries over a size limit).
- **Checkpoint:** stage everything and commit in the shadow repo. The `Checkpoint` record stores the shadow commit SHA.
- **Rollback:** restore files from that commit and delete files created after it that are inside checkpoint scope. The user's `.git` is never touched.
- Old checkpoints are pruned by retention policy.

## Alternatives considered
`git stash` (mutates user state, Git-only); full directory copies (slow, disk-heavy); worktrees only (Git-only).

## Consequences
Ignored files (e.g. `node_modules`, `.venv`) are not checkpointed; rollback reports this. Requires the `git` CLI on PATH (already required for Git tools).
