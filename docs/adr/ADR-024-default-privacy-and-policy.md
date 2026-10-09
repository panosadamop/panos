# ADR-024: Default privacy mode and policy configuration

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** BR-004, PERMISSIONS, NFR-SEC-001, AC-US005-01

## Context
The spec did not define the default privacy mode, the default P2 behavior or the delete-impact rule (decision D-3).

## Decision
- New and registered projects default to **LOCAL_ONLY**. Changing to STANDARD is an explicit, audited user action.
- **P2 defaults to ask** (REQUIRE_APPROVAL), unless the user enables auto-allow for a listed P2 class globally or per project.
- **File deletion:**
  - P2 for single files recoverable from Git (tracked and clean) or from a Panos checkpoint;
  - P3 for bulk deletes (more than 20 files or any directory), untracked/ignored files not covered by a checkpoint, or anything outside recovery coverage.
- Local branch creation: P1.

## Alternatives considered
Default STANDARD (cloud-eligible): convenient but not fail-safe.

## Consequences
Cloud models are unused until the user marks a project STANDARD. Onboarding explains this.
