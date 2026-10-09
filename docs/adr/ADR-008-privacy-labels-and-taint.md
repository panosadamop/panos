# ADR-008: Privacy labels and taint propagation

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** BR-004, NFR-SEC-005, NFR-SEC-004, NFR-PRV-004, AI_ROUTING (hard rule), US-005, AC-US005-01..05

## Context
"Local-only content and sensitive derived context" must never reach cloud AI. Derived context includes summaries, embeddings, tool outputs, error messages, transcripts and screenshots.

## Decision
- Every content item carries a privacy label: `LOCAL_ONLY` > `STANDARD`. Labels come from the project/folder policy or the source type; screenshots are `LOCAL_ONLY` (ADR-025).
- Anything derived from content inherits the **maximum** label of its inputs.
- A context bundle sent to a model has the maximum label of its parts.
- A **job or conversation becomes stickily tainted** once it has processed `LOCAL_ONLY` content. Every later model call in it is local-only.
- **Two enforcement points:**
  1. The router hard-filters providers by eligibility (ADR-009).
  2. Every cloud adapter checks the label at its egress chokepoint and refuses `LOCAL_ONLY` payloads with an audit event.
- `STANDARD` payloads to cloud providers pass secret redaction first.

## Alternatives considered
Router-only enforcement (a single point of failure); per-message classification by a model (non-deterministic).

## Consequences
Some conversations will be locked to local models after touching a local-only file; the UI shows the taint state. Property-based tests assert that no cloud adapter call happens for any tainted context.
