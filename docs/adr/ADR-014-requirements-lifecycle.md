# ADR-014: Requirements lifecycle, versioning and hierarchical approval

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** BR-009, BR-010, FR-008, FR-009, FR-010, US-031, US-032, AC-US031-01..04, AC-US032-03

## Context
The specs disagreed on states (FR-010 vs REQUIREMENTS_PIPELINE vs DATA_MODEL) and did not define cascade semantics when a parent changes.

## Decision
**Lifecycle:** `DRAFT → PENDING_APPROVAL → APPROVED | REJECTED`, plus `SUPERSEDED`, and an orthogonal `stale: bool` linked to an impact-analysis task.

**Versioning:** requirements are **immutable versions** identified by (`requirement_key`, `version`). Editing creates a new DRAFT version; the previous approved version becomes SUPERSEDED when the new one is approved.

**Cascade:** approving a parent cascades to all descendants at their **current versions**, except explicitly excluded descendants (and their subtrees). Each cascaded descendant gets its own `RequirementApproval` with `cascaded_from_id`.

**Change control:** a new version of an approved item marks its descendants and trace-linked downstream items `stale=true` and creates an impact-analysis task. Implementation of stale items is blocked until they are re-approved or the stale flag is cleared by an approved impact analysis.

## Alternatives considered
Mutable rows with an audit trail only (loses exact approved content).

## Consequences
Storage grows with versions (acceptable). Traceability queries must pick the latest approved version.
