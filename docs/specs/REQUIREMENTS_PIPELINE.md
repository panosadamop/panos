# Requirements-to-Implementation Pipeline

> **Amendments (2026-10-08, ADR-014):** "potentially stale" is modelled as a `stale` flag orthogonal to the lifecycle state. The traceability example below is illustrative only (FR-032 does not exist; BR-014 is estimation).


## Pipeline
Brief → ambiguity analysis → interview → BRD → FRD → NFR → architecture → ERD/data model → APIs/OpenAPI → Epics → Features → Stories → acceptance criteria → test cases → estimates → sprint plan → approval → implementation → verification.

## IDs
Use stable IDs such as BR-001, FR-001, NFR-SEC-001, EPIC-001, FEAT-001, US-001, AC-US001-01, TC-001, API-001, ADR-001.

## Traceability
Every implementation feature should trace backward to approved requirements and forward to tests/evidence.

Example:
BR-014 → FR-032 → EPIC-005 → US-041 → AC-US041-01 → API-017 → TC-088 → code symbols/commit evidence.

## Approval
Items are versioned and may be DRAFT, PENDING_APPROVAL, APPROVED, REJECTED, SUPERSEDED. Parent approval may cascade to descendants unless exclusions are selected.

## Change control
Changing an approved upstream requirement marks affected downstream artifacts as potentially stale and creates an impact-analysis task before implementation continues.
