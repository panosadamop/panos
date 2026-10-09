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

## Implementation notes (M5, 2026-10-09)

- **Shadow repo:** `<data>/checkpoints/<project_id>.git`, used with `GIT_DIR`/`GIT_WORK_TREE`, its own identity and `--no-verify`. It never reads or writes the user's `.git`. Verified with byte-identical HEAD, index and stash before and after a rollback.
- **`info/attributes`** sets `* -text`, so checkout restores exact bytes (CRLF and binary verified). **`info/exclude`** lists `.git/`, `__pycache__/`, `*.pyc`, tool caches, `.idea/` and `.vs/`. The project's own `.gitignore` files apply too; ignored files are left untouched by rollback.
- **Rollback** first commits the current state as an undo point (`safety_reference`). It then deletes files created after the checkpoint, restores modified and deleted files, and verifies that nothing differs from the checkpoint.
- **Job integration:** a checkpoint is taken before the first edit. `POST /jobs/{id}/rollback` and `panos job rollback` are refused while the job is running.
- **Not yet:** retention pruning (`jobs.checkpoint_retention` is configured but unused), a large-file size limit, and nested Git repositories (recorded as gitlinks, contents not restored).
