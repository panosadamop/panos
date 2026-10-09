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

## Implementation notes (M4, 2026-10-09)

- **Labels everywhere:** every `Message` carries `privacy` (default `LOCAL_ONLY`, fail-safe) and `trust`. A `ChatRequest`'s label is the maximum of its messages; a retrieval bundle's label is the maximum of its chunks; an assistant reply inherits the request's label.
- **Sticky conversations:** a conversation starts with its project's mode (unscoped: `ai.unscoped_privacy`, default `LOCAL_ONLY`). Each turn re-applies the project's *current* mode, which can only tighten the label, never relax it. Relaxing a project later does not untaint existing conversations.
- **Enforcement points (both active):**
  1. the router's first hard filter;
  2. `AdapterBase._guard` / `_guard_texts`, called first in every adapter method (chat, stream, embed). It applies to any endpoint classified as cloud, including an Ollama/OpenAI-compatible server that is not on loopback or an explicitly trusted LAN. Cloud adapters also refuse requests the gateway has not redacted (`redacted=False`).
- **Audit:** cloud calls are recorded as `ai.cloud_call` (provider, model, label, tokens, cost; no content). Adapter refusals are recorded as `ai.egress_blocked`.
- **Redaction for cloud:** known credential formats plus quoted key/value literals (`code_safe` mode). Identifiers such as `token = request.token` and hashes are kept, so code is not corrupted.
- **Embeddings follow labels:** `LOCAL_ONLY` chunks can only be embedded by a local model.
- **Tests:** a Hypothesis property test over random candidate sets; a full-stack suite with an always-healthy, preferred cloud spy (`tests/security/test_local_only_egress.py`); direct adapter-guard tests (the HTTP route or SDK is never touched).
