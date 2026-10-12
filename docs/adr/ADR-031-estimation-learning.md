# ADR-031: Estimation learning from approved actuals

- **Status:** Proposed (for acceptance at the M10 exit review)
- **Date:** 2026-10-12
- **Traces:** BR-014, BR-015, KNOWLEDGE_MEMORY "estimation memory", ADR-014

## Context
M6 estimates hours from story points with a fixed hours-per-point and role split. BR-015 asks Panos to retain *approved* estimated-versus-actual metrics to improve future estimates. Owner decisions (2026-10-12): actuals come from hours the owner logs, tracker worklogs and Panos job metrics; learning **suggests**, and the owner approves.

## Decision
1. **Baselines.** When a project is estimated (`panos project estimate`, exports, sprint plan), each story's points, estimated hours and the project's technologies are recorded as a versioned baseline. The latest baseline before work started is what actuals are compared with.
2. **Actuals.** Each actual has a requirement key, source, hours, role, date and an optional external reference and cost.
   - **Sources:** `manual` (logged by the owner), `tracker` (Jira worklogs / ADO completed work on linked work items), `job` (Panos jobs).
   - Manual entries are approved when logged by the owner. Tracker imports arrive **pending**; the owner approves or rejects them.
   - Job metrics are a separate **machine** series (runtime, tokens, cost). They are not mixed into human hours.
3. **Calibration.** Only approved human actuals for stories that have a baseline and are complete are used.
   - **Factor:** a per-story ratio (actual hours ÷ estimated hours) gives factors per project, per technology and overall. A median is used for robustness; the spread is reported as an interquartile range.
   - **Accuracy:** bias and MAPE.
   - **Threshold:** a suggestion needs at least 5 stories (configurable).
   - **Suggestion:** new hours-per-point, overall and per technology. It never applies itself.
4. **Approval.** An approved suggestion becomes a versioned calibration (who approved it, when, and the data it was based on). Estimation uses the most specific applicable calibration: project technology first, then overall, then the configured default. It states this in the estimate assumptions ("calibrated from N stories, approved …"). Rejected suggestions are recorded. Everything is audited.
5. **Privacy.** Actuals and calibrations are project metadata on the node. Only aggregate numbers, never requirement text, are shared with the hub analytics.

## Alternatives considered
- **Automatic calibration:** rejected by the owner.
- **Regression on many features:** too little data for a personal tool; a robust ratio is explainable.

## Consequences
- Estimates improve only as fast as the owner logs and approves actuals.
- Tracker imports depend on the work items being linked to requirements (M8-06 evidence links).

## Implementation notes (M10, 2026-10-12)

**Data:** migration 0008 (`estimate_baseline`, `effort_actual` with `(source, external_ref)` unique, `calibration`). Code: `panos_core.effort` (`service`, `calibration`, `trackers`), node API `/effort/*` and `/projects/{ref}/effort*`, CLI `panos effort …`, desktop **Effort** view, dashboard view through the hub (`effort` query).

**Rules:**
- The suggested hours-per-point is the median of each story's actual hours ÷ points (correct even when older baselines used another value); the factor (median ratio) measures estimate accuracy.
- A story counts once an approved entry marks it finished (`--done`, or the tracker item is closed).
- An estimate records a baseline only when asked (`POST /projects/{ref}/estimate/baseline`, `panos effort baseline`). Plain estimates and exports do not record one.
- Jobs are recorded on COMPLETED/FAILED and attributed to requirements through `job:<id>` evidence links, otherwise to `*` (project level).

**Tests:** golden calibration math; the full flow on a real node (log → suggest → approve → estimate changes; superseding; rejection); pending tracker imports count only after approval; Jira/ADO import with deduplication and the ADO delta; job metrics; API/CLI.
