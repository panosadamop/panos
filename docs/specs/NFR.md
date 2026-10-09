# Non-Functional Requirements

> **Amendments (2026-10-08, approved plan D-11):** IDs added to previously unnumbered NFRs (REL, PERF, PORT, MNT, OBS, ACC) and quantified targets added. Original wording preserved.

## Security
- NFR-SEC-001 Least privilege by default.
- NFR-SEC-002 No unrestricted LLM shell/desktop access.
- NFR-SEC-003 Encrypt credentials/secrets at rest using OS-backed secure storage where possible.
- NFR-SEC-004 Never include secrets in logs/prompts unless explicitly required and policy permits.
- NFR-SEC-005 Local-only content must be technically blocked from cloud-provider requests.
- NFR-SEC-006 Remote nodes use mutual authentication, encrypted transport and revocable device identity.

## Privacy
- NFR-PRV-001 Wake-word detection operates locally.
- NFR-PRV-002 Screenshots are ephemeral by default.
- NFR-PRV-003 Knowledge sources retain provenance and access scope.
- NFR-PRV-004 Project privacy policy overrides model preference/cost.

## Reliability
- NFR-REL-001 Persist job state before consequential steps.
  *Target:* 100% of P2+ tool steps have a persisted step record before execution (enforced by the tool runtime).
- NFR-REL-002 Recover safely after runtime/UI crashes.
  *Target:* interrupted jobs recover to `PAUSED` (reason `interrupted`); no consequential action is executed twice.
- NFR-REL-003 Tool calls are idempotent where feasible.
  *Target:* every tool declares `idempotent`; non-idempotent tools are never auto-retried.
- NFR-REL-004 Failed tasks must not be reported as successful.
  *Target:* `COMPLETED` requires all verification steps passed or explicitly reported as skipped.

## Performance
- NFR-PERF-001 Wake-word detection should feel immediate on supported hardware.
  *Target:* ≤ 300 ms from end of wake word to visual feedback on reference hardware.
- NFR-PERF-002 Simple local commands should not require a cloud round trip.
  *Target:* "open X", "list projects", "pause" handled locally, < 1.5 s end-to-end from text input.
- NFR-PERF-003 UI remains responsive while agents/tools run.
  *Target:* no UI main-thread block > 100 ms; local API read p95 < 100 ms.
- NFR-PERF-004 Indexing runs incrementally after initial project ingestion.
  *Target:* re-index processes only files whose content hash changed.

## Portability
- NFR-PORT-001 Core business logic is OS-neutral. *Enforced by import-linter.*
- NFR-PORT-002 Windows/macOS/Linux behavior lives behind adapter contracts. *One contract test suite per adapter, run in a CI OS matrix.*
- NFR-PORT-003 AI/STT/TTS/vector/integration providers are pluggable.

## Maintainability
- NFR-MNT-001 Typed interfaces and versioned schemas. *mypy `--strict`, TypeScript `strict`, versioned API and event schemas.*
- NFR-MNT-002 High unit coverage for policy/permission/routing logic.
  *Target:* ≥ 95% branch coverage for policy, approvals, routing, privacy and job state machine; ≥ 80% line coverage for `panos_core` overall.
- NFR-MNT-003 Architecture Decision Records for major decisions (`docs/adr/`).
- NFR-MNT-004 No provider-specific behavior in domain services. *Enforced by import-linter.*

## Observability
- NFR-OBS-001 Structured logs with correlation/job/step IDs.
- NFR-OBS-002 Metrics for latency, failures, AI tokens/cost, tool outcomes and queue depth.
- NFR-OBS-003 Local diagnostic bundle with secret redaction.

## Accessibility
- NFR-ACC-001 Core functionality usable without voice.
- NFR-ACC-002 Keyboard-accessible desktop/dashboard controls. *jsx-a11y lint + axe checks.*
- NFR-ACC-003 Voice output can be muted independently.
