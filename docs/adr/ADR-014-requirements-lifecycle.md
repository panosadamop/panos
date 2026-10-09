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

## Implementation notes (M6, 2026-10-09)

- **Storage:** tables `requirement` (one row per `(project, key, version)`; content immutable, only status/stale change), `requirement_approval`, `trace_link`, `interview`, `generated_skill` (migration 0006).
- **Keys:** allocated per project and type (`FR-007`, `NFR-001`, `ASSUMPTION-002`). Acceptance criteria are numbered under their story (`AC-US003-02`).
- **Hierarchy and links:** the hierarchy is `parent_key` (BR → FR → EPIC → FEAT → US → AC → TC). Other relations are version-independent trace links (`realizes`, `verifies`, `implements`, `depends_on`, `relates`). A link target can also be implementation evidence (`commit:…`, `file:…`, `job:…`, `test:…`).
- **Editing:** creates a new DRAFT version. An unapproved previous draft becomes SUPERSEDED at once. If any version was ever approved, every downstream item is marked stale: descendants, plus items that trace *to* an affected item, transitively.
- **Impact-analysis task:** a stale-marking edit creates an `impact_analysis` job. The job waits (WAITING_FOR_USER) until every affected item is re-approved or explicitly cleared (`clear_stale`, with a reason, audited); resuming it re-checks.
- **Cascade approval:** approves the latest versions of the item and its descendants, except excluded subtrees. Each item gets a record with `cascaded_from_id`; previously approved versions become SUPERSEDED, and `stale` is cleared.
- **Implementation gate:** a coding job whose objective names project requirements that are not APPROVED, or are stale, is BLOCKED before any change is made.
- **Traceability query:** trees from each BR with links, evidence and gaps. A gap is an item missing its expected downstream type: BR→FR, FR→EPIC/FEAT/US, US→AC, AC→TC.
