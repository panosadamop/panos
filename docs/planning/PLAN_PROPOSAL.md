# Panos — Implementation Plan

Status: **APPROVED 2026-10-08** (decisions below). Execution is milestone-gated; see `docs/planning/reports/`.
Date: 2026-10-08
Inputs: all specification documents (now in `docs/specs/`).

## Approved decisions (2026-10-08)

| # | Decision |
|---|---|
| D-1 | Repository moved to `C:\Users\padamop\PycharmProjects\panos` |
| D-2 | Windows is the first production OS adapter |
| D-3 | New/registered projects default to LOCAL_ONLY |
| D-4 | Ollama is the default local runtime. Reference hardware measured: Ryzen 7 PRO 7735U, Radeon 680M iGPU (no discrete GPU), 62 GB RAM |
| D-5 | Anthropic + OpenAI (and OpenAI-compatible) cloud providers first |
| D-6 | Personal/internal use (GPL/non-commercial voice components acceptable) |
| D-7 | Specs moved to `docs/specs/` |
| D-8 | IMPLEMENTATION_PLAN milestone order |
| D-9 | `git init` done; commits only with explicit approval |
| D-10 | uv installed; Rust updated |
| D-11 | NFR targets and new stories US-005/024/044/045 accepted |
| D-12 | S-1 voice feasibility spike run during M0 |

Contents
1. Specification analysis summary
2. Inconsistencies, missing decisions and risks
3. Proposed Architecture Decision Records
4. Proposed ID additions (NFR IDs, AC numbering, missing stories)
5. Implementation backlog (by milestone, with traceability)
6. Requirement coverage matrix (reverse mapping)
7. Monorepo structure
8. Libraries and rationale
9. Platform-specific functionality
10. MVP boundary and milestone dependencies
11. Per-milestone working protocol
12. Decisions needed from you

---

## 1. Specification analysis summary

Panos is a local-first assistant with one identity. Its core is a Python **Local Node** that contains the API, orchestrator, agents, policy, tools, knowledge, voice, jobs and SQLite. A Tauri/React desktop shell and a CLI sit in front of it. A control plane, web dashboard and mobile app come later.

The defining invariants, which drive most of the design, are:

| Invariant | Source |
|---|---|
| LLMs never get unrestricted shell, filesystem or desktop access. Every action goes typed tool → policy → approval → audit. | CLAUDE.md, README, NFR-SEC-002, FR-011 |
| Local-only content, including derived context, is technically blocked from cloud providers. | BR-004, NFR-SEC-005, AI_ROUTING |
| No email-send capability exists in the default runtime. | BR-019, AC-US043-02 |
| Approvals are bound to the exact payload. If the payload changes, the approval is invalid. | PERMISSIONS, SECURITY |
| Long-running work is persisted, cancellable, limited and checkpointed. | FR-022..024 |
| OS, AI, STT/TTS, vector store and integrations sit behind adapters. Domain code stays vendor-neutral. | NFR Portability/Maintainability |

The spec is strong on principles and governance. It is thin on concrete mechanisms, quantified NFRs and acceptance criteria. Section 2 lists the gaps.

---

## 2. Inconsistencies, missing decisions and risks

### 2.1 Inconsistencies

| # | Issue | Where | Proposed resolution |
|---|---|---|---|
| I-1 | Milestone order conflict. IMPLEMENTATION_PLAN has Requirements (M6) before Desktop/Voice (M7). ROADMAP has Voice+Desktop (Phase 2) before Requirements Studio (Phase 3). | IMPLEMENTATION_PLAN vs ROADMAP | Follow IMPLEMENTATION_PLAN; README marks it as the operative plan. Allow M7 UI work to start once M5 APIs are stable (see §10). |
| I-2 | MVP item 17 ("basic local knowledge indexing/RAG") is in the MVP, but knowledge ingestion only appears in M8, which is post-MVP. | MVP vs IMPLEMENTATION_PLAN | Move basic local indexing into M3 (lexical) and M4 (vector/hybrid). Keep broad adapters in M8. |
| I-3 | The approval service (M2) needs jobs to enter `WAITING_FOR_APPROVAL`, but the job state machine is in M5. | IMPLEMENTATION_PLAN | Move the core state machine (transition table and persistence) into M2. M5 adds the runner, limits and recovery. |
| I-4 | Requirement lifecycle states differ. FR-010 has approved/rejected/pending/superseded. REQUIREMENTS_PIPELINE has DRAFT/PENDING_APPROVAL/APPROVED/REJECTED/SUPERSEDED. DATA_MODEL has both `status` and `approval_state`. Change control mentions "stale", which is not in any state list. | FR-010, REQUIREMENTS_PIPELINE, DATA_MODEL | Use one lifecycle: `DRAFT → PENDING_APPROVAL → APPROVED / REJECTED`, plus `SUPERSEDED`, plus an orthogonal `stale` flag with an impact-analysis link (ADR-014). |
| I-5 | DATA_MODEL `Approval` is shaped for tool-action approvals (payload_hash, expires_at). It lacks fields that requirement approvals need (approver, version, comments, exclusions, cascaded_from) and fields PERMISSIONS mandates (`target`, `user decision`, `decided_by`). | DATA_MODEL vs PERMISSIONS/FR-010 | Use two entities: `ActionApproval` (single-use, hash-bound, expiring) and `RequirementApproval` (versioned, hierarchical). Both are audited. |
| I-6 | The REQUIREMENTS_PIPELINE example uses `FR-032` (FRs stop at FR-030) and maps BR-014 (estimation) to US-041 (Docker). | REQUIREMENTS_PIPELINE | Treat it as illustrative only. No action beyond noting it. |
| I-7 | AC IDs use the form `AC-US001` with unnumbered bullets, while the pipeline prescribes `AC-US001-01`. | ACCEPTANCE_CRITERIA | Number the bullets in order (§4.2). |
| I-8 | REPOSITORY_STRUCTURE places specs in `docs/specs/`, but they currently live in the repo root and README links to them there. | REPOSITORY_STRUCTURE vs README | Move them to `docs/specs/`, keep `CLAUDE.md` and `README.md` at root, and update links (decision D-7). |
| I-9 | Browser research (FR-016) and Jira/Azure DevOps read+prepare (BR-016) appear in no milestone of IMPLEMENTATION_PLAN. | IMPLEMENTATION_PLAN | Add both to M8. Add the P4 policy class for browser submissions to M2 now. |
| I-10 | ARCHITECTURE puts `global_hotkey` and `notification` in the Python OS adapter. When the desktop runs, Tauri is the natural owner of both. | ARCHITECTURE | The Tauri shell owns hotkey, tray and notifications. The Python adapter keeps a `NotificationSink` port, with a console/CLI implementation used when no UI is attached (ADR-015). |

### 2.2 Missing technical decisions (each becomes an ADR in §3)

- Process topology: a daemon versus a process owned by the UI, single-instance behavior, autostart, and how the CLI reaches the node.
- Loopback authentication: token issuance and storage, Host/Origin validation (DNS rebinding), and WebSocket authentication.
- How a "controlled shell" differs from "arbitrary shell": command parsing, classification into action classes, and stopping an approval bypass through `git commit` or `docker run` typed into a shell.
- What counts as "sensitive derived context", and how taint propagates through conversations, jobs, embeddings and summaries.
- Default privacy mode for new or registered projects.
- Checkpoint mechanism, including for projects that are not git repositories.
- Whether the coding agent is a Panos-native loop or delegates to an external coding agent such as Claude Code or Aider. External agents bring their own shell tools, which conflicts with NFR-SEC-002.
- Default P2 ("configurable") behavior and how "delete: P2/P3 based on impact" is computed.
- Approval token format, single-use semantics and replay protection.
- Local LLM runtime and the minimum hardware profile.
- Voice stack: custom wake word "Panos", Greek and mixed-language STT/TTS, and licensing.
- Packaging the Python runtime with Tauri, plus code signing and notarization.
- Audit log integrity and retention.
- Event and WebSocket schema versioning, and TypeScript type generation.

### 2.3 Gaps in the spec

| # | Gap | Proposal |
|---|---|---|
| G-1 | NFRs other than SEC and PRV have no IDs. Performance NFRs are not quantified ("feel immediate"). | Assign IDs (§4.1) and propose targets (§4.1) for approval. |
| G-2 | Only 9 of 26 user stories have acceptance criteria. | Draft ACs for the other 17 in M1-00, for your approval. |
| G-3 | BR-003, BR-004, BR-020, BR-022, BR-027 and BR-028 have no user story. The most important security rule (local-only) has no story or AC. | Propose US-005 (local-only), US-024 (offline), US-044 (calendar approval) and US-045 (browser boundary) (§4.3). |
| G-4 | The data model lacks Conversation/Message, ToolInvocation, Application/Alias, ProjectPolicy, GeneratedSkill, AIUsage (tokens/cost), Notification and Interview. | Add them as the milestones that need them arrive. Each schema change goes through Alembic. |
| G-5 | The API lacks endpoints for apps, tools, checkpoints/rollback (US-023), conversation history, voice control, AI providers/models/budgets, knowledge, skills and estimates. The WebSocket event schema is undefined. | Add endpoints per milestone. Define the event envelope `{v, type, id, ts, correlation_id, job_id?, payload}` (ADR-018). |
| G-6 | There is no minimum or reference hardware profile, so local-capable coding models and local Greek STT quality cannot be planned. | Decision D-4. |
| G-7 | UI language (Greek/English UI strings) is unspecified. | i18n from day one (el/en). Responses use the language of the user's input. |
| G-8 | No intent layer is defined for turning conversation messages into jobs or direct tool requests. | Intent classification on the local lightweight tier (M4-09). Anything with side effects becomes a Job. |

### 2.4 Implementation risk register

| # | Risk | Likelihood / Impact | Mitigation |
|---|---|---|---|
| R-1 | **Shell tool becomes de-facto arbitrary shell** (for example `npm test` running arbitrary scripts, or `git commit` typed into a shell). | High / Critical | ADR-007: argv-only execution, parser-based classification (bash via `bashlex`, PowerShell via its own AST parser), unknown commands default to P2/P3, approval-bypass security tests, env scrubbing, cwd confinement. Remaining risk: test scripts can still run repository code; this is documented and accepted. |
| R-2 | **Local-only leakage through derived context** (summaries, embeddings, error messages, screenshots). | Medium / Critical | ADR-008: privacy labels on all content, sticky job taint, a single egress chokepoint in the cloud adapters that rejects tainted payloads, and property-based plus e2e network-spy tests. |
| R-3 | **Custom Greek wake word "Panos"** has poor accuracy or false positives (it is a common name). | High / High | Early time-boxed feasibility spike (S-1). Fall back to push-to-talk/hotkey. Adapters exist for openWakeWord (custom-trained) and Porcupine. Sensitivity is configurable. |
| R-4 | **Greek and mixed-language STT/TTS quality offline.** *S-1 measured:* `small` = 2.7 s per command at Greek CER 9 %; `medium`/`turbo` = 8–11 s on CPU; no Greek Windows voice installed; Greek voices garble English terms. | High / Medium | ADR-016: `small` int8 default with Greek default language + vocabulary prompt; accurate mode opt-in; Piper `el_GR-joy-medium` + `en_US-lessac-medium` with a language-run splitter. Follow-ups: MKL A/B, whisper.cpp + Vulkan, real-voice evaluation. |
| R-5 | **Local models too slow or weak** for local-only coding. *S-1 measured (CPU-only):* 3B 11–13 tok/s, 7B coder 5–7 tok/s, 14B 2.8 tok/s; prompt eval 12–64 tok/s; weak Greek prose. | **High** / High | ADR-009: local-only coding runs as background jobs with trimmed context and a duration estimate; router shows the shortfall instead of silently degrading; follow-ups: iGPU via `OLLAMA_IGPU_ENABLE=1`, MoE model, Greek-capable local model. |
| R-6 | **Prompt injection** from repositories, web pages or documents. | High / High | ADR-023: untrusted-content envelopes, policy independent of model output, exact-payload approvals shown to the user, injection test corpus. |
| R-7 | **Wayland limitations on Linux** (global hotkeys, active-window metadata, capture). | Certain / Medium | Documented degraded mode. Use xdg-desktop-portal (GlobalShortcuts, Screenshot) where available, full support on X11. |
| R-8 | **macOS permissions** (Screen Recording, Accessibility, Microphone) and notarization. | Certain / Medium | Permission-onboarding UX. Signing and notarization when packaging (ADR-021). |
| R-9 | **Repository lives in OneDrive.** Syncing `.venv`, `node_modules`, Rust `target/` (GBs) and live SQLite files causes lock and corruption risk. | High / Medium | Decision D-1: move the repo out of OneDrive. Panos runtime data goes to `platformdirs` locations, never synced folders. |
| R-10 | **Rust 1.83 toolchain** may be too old for current Tauri 2 dependencies. uv is not installed. | Medium / Low | `rustup update stable` and install uv before M1 (D-10). |
| R-11 | **Licensing.** Piper's maintained fork is GPL-3.0, openWakeWord's pre-trained models are CC BY-NC-SA, and Porcupine is commercial. | Medium / Medium | Decision D-6: personal/internal versus distributed product. All voice engines stay behind adapters so they can be swapped. |
| R-12 | **Gmail has no draft-only OAuth scope.** `gmail.compose` permits sending. | Certain / Medium (M8) | Enforce in code: no send tool, the HTTP adapter cannot reach send endpoints (URL allowlist), plus a security test. Microsoft Graph can avoid `Mail.Send`. |
| R-13 | **Scope size.** The MVP alone is very large (18 capabilities). | High / High | Vertical slices, strict milestone exits, defer anything not in MVP.md. |
| R-14 | **`sqlite-vec` is pre-1.0.** | Medium / Low | Hide it behind a `VectorIndex` port. The fallback is brute-force cosine over stored vectors for small corpora. |

---

## 3. Proposed Architecture Decision Records

Each ADR will be written to `docs/adr/ADR-NNN-*.md` (Context / Decision / Alternatives / Consequences / Traces) during M1 and accepted with you before it is relied on.

| ADR | Decision (proposed) | Main alternatives rejected | Traces |
|---|---|---|---|
| ADR-001 Monorepo tooling | **uv workspace** for Python, **pnpm workspace** for TS, Cargo only inside `apps/desktop/src-tauri`. Root task runner: `just` (or `scripts/*.py` if you prefer no extra tool). | Poetry (slower, weak workspaces), Nx/Turborepo (unnecessary at this size) | NFR-MNT-001 |
| ADR-002 Process topology | The Python node is a **standalone per-user daemon** (single instance, lock file). The desktop starts it as a Tauri sidecar if it isn't running, or attaches to it. The CLI is a thin HTTP client that can auto-start the daemon. The node can run headless (CLI-only). | Python embedded in the UI process; CLI running core in-process (that would duplicate state and job ownership) | BR-001, BR-023, NFR-REL-002 |
| ADR-003 Local API security | Bind to `127.0.0.1` on a random port. Random 256-bit bearer token in a user-only file (`0600` / owner ACL) in the runtime dir. Host header allowlist and Origin check (DNS-rebinding defense). The WebSocket authenticates in its first message. No CORS wildcard. No arbitrary shell endpoint. | Unix sockets / named pipes only (harder for WebView and cross-platform), no authentication | API.md security, NFR-SEC-001 |
| ADR-004 Persistence | **SQLite (WAL)** with **SQLAlchemy 2.0** (async, aiosqlite) and **Alembic**. Domain entities are pure Pydantic/dataclass models. ORM mapping and repositories live in `persistence/`. | SQLModel (couples domain to ORM), raw SQL | DATA_MODEL, NFR-MNT-001 |
| ADR-005 Policy engine | In-house, **pure-function policy evaluator**, default deny. Inputs: action class, permission level, target, project policy, privacy taint, actor, job context. Output: `ALLOW / REQUIRE_APPROVAL / DENY` with reasons and the policy version. Default policy is YAML mirroring PERMISSIONS.md. Project overrides may **tighten** freely. Loosening P2→P1 is allowed only for listed P2 classes, by an explicit user action that is audited. P3/P4 can never be loosened by config. | OPA/Rego (extra runtime binary, steep for a single-user app), Casbin (RBAC-oriented) | NFR-SEC-001, FR-011, PERMISSIONS |
| ADR-006 Approval binding | Payload canonicalized with **RFC 8785 JCS**, hashed with SHA-256, and wrapped in a token HMAC-signed with a node key from the OS keyring. Tokens carry expiry and are **single-use** (marked consumed atomically). At execution time the payload is rebuilt and re-hashed, and any mismatch aborts with an audit event. Git commit payloads include the staged tree hash. | Plain DB flag, payload comparison without canonicalization | PERMISSIONS, AC-US040-02, AC-US041-03, security tests (forged/replayed) |
| ADR-007 Controlled shell | Commands are **argv lists executed without a shell** by default. Shell profiles (pwsh, powershell, bash, zsh, WSL) exist for user-initiated scripts at ≥P2. Every command is **parsed and classified** into action classes (git-mutation, docker-exec, kubectl-mutation, package-install, network-upload, file-delete…) using bashlex for POSIX and the PowerShell AST parser. Classification takes the highest risk of any sub-command. Unparseable input is treated as P3. Env is scrubbed (secret variables removed unless allowlisted). Cwd stays inside the project. Timeouts. Process-tree kill (psutil, plus Job Objects on Windows). Output is capped. | Free-form shell string with denylist (bypassable), container-only execution (too heavy for MVP; later option) | FR-012, NFR-SEC-002, SECURITY ("bypassing approval by alternative tool") |
| ADR-008 Privacy labels & taint | Every content item (file read, retrieval chunk, tool output, screenshot, transcript, summary, embedding) carries a label: `LOCAL_ONLY` > `STANDARD`. Context bundles take the max label. Jobs and conversations become **stickily tainted** once they touch LOCAL_ONLY content. The router hard-filters providers by eligibility. **Defense in depth:** cloud adapters refuse tainted payloads at the egress chokepoint and audit the attempt. Secret redaction runs on all STANDARD cloud payloads. | Router-only enforcement (single point of failure) | BR-004, NFR-SEC-005, NFR-PRV-004, AI_ROUTING hard rule |
| ADR-009 AI provider abstraction | Provider-neutral ports: `ChatModel` (streaming, tool calls), `EmbeddingModel`, `VisionModel`. Adapters: **OpenAI-compatible HTTP** (covers Ollama, llama.cpp server, LM Studio, vLLM), **Anthropic**, **OpenAI**, and a scripted mock for tests. Capability registry in YAML. A provider counts as "local" only if its endpoint is loopback, or a LAN host explicitly trusted by you. | LiteLLM (broad but heavy, adds a large dependency surface into the privacy-critical path), LangChain model wrappers | BR-003, FR-021, NFR-PORT-003, NFR-MNT-004 |
| ADR-010 Orchestration framework | **In-house orchestrator** with an explicit, table-driven job state machine and an agent-role contract. No LangGraph/LangChain/CrewAI in the core. | LangGraph (own persistence/state semantics would compete with Panos jobs, approvals and audit) | AGENTS, FR-022, NFR-MNT-001 |
| ADR-011 Job durability | asyncio job runner inside the node. Job and steps are persisted **before** every consequential step. After a crash, jobs that were RUNNING become `PAUSED` (reason `interrupted`) and need an explicit resume, so there is no blind re-execution. Tools declare idempotency. | Celery/Redis/Temporal (wrong fit for a single-user local-first app) | FR-022, NFR-REL-001..004, AC-US050-01 |
| ADR-012 Checkpoints | A **shadow git repository** per project under the Panos data dir (separate `GIT_DIR`, `GIT_WORK_TREE`=project). It respects `.gitignore` and has size limits. It never touches the user's history, stash or index, and it works for non-git projects. Rollback restores the files at task level. Optional git-worktree isolation comes later. | `git stash` (mutates user state), full file copies (slow), worktrees only (needs git projects) | FR-024, US-023, AC-US020-02 |
| ADR-013 Coding executor | A **Panos-native agent loop** using only Panos tools: structured edit (search/replace patch with preimage check), fs, shell, git read. External coding agents (Claude Code CLI etc.) are not used in the MVP because they carry their own unrestricted tools. They could be reconsidered later as a sandboxed adapter. | Delegate to Claude Code/Aider | NFR-SEC-002, FR-006 |
| ADR-014 Requirements lifecycle & approval | Unified lifecycle (I-4). Requirements are immutable versions (`requirement_key` + `version`). A parent approval cascades to descendants at their **current versions**, minus exclusions, and the cascade is recorded per descendant. Editing an approved item creates a new DRAFT version. Descendants and dependents traced from it get `stale=true`, and an impact-analysis task is created. | Mutable rows with an audit trail only | BR-010, FR-010, US-031, AC-US031-*, change control |
| ADR-015 Desktop shell responsibilities | **Tauri (Rust) owns** window, tray, global shortcut, desktop notifications, autostart, single instance and sidecar lifecycle. **Python owns** app discovery/launch, window focus/metadata, capture, voice and all logic. The frontend gets a minimal Tauri capability set: no fs plugin, and the shell plugin is limited to the sidecar. | Electron (heavier), hotkeys in Python (pynput etc., weaker cross-platform and Wayland issues) | ARCHITECTURE, FR-001, FR-025 |
| ADR-016 Voice stack | Voice runs in a **separate worker process**. Ports: `WakeWordDetector`, `VAD`, `SpeechToText`, `TextToSpeech`, `AudioIO`. Initial adapters: openWakeWord (custom "Panos" model), Silero VAD (ONNX), faster-whisper (multilingual, auto language ID), and TTS via an OS-native adapter (SAPI / AVSpeechSynthesizer / speech-dispatcher) plus optional Piper. Audio stays in memory only. Barge-in "stop talking" goes to TTS control, while `stop/pause/resume` go to job control. | Porcupine as default (license/key), Vosk (lower Greek accuracy) | BR-005, FR-001/002, VOICE, NFR-PRV-001 |
| ADR-017 Knowledge index | Lexical search with **SQLite FTS5**, vectors with **sqlite-vec** (both behind ports), fused with RRF. Local multilingual embeddings via **fastembed** (ONNX, no torch); the model is chosen by a small Greek/English benchmark. Incremental indexing by content hash, honoring `.gitignore`. The index DB is separate from the state DB. LOCAL_ONLY vectors are never exported. | Chroma/Qdrant/LanceDB (extra services or deps; can be added later behind the port) | FR-019/020, KNOWLEDGE_MEMORY, NFR-PRV-003, NFR-PERF-004 |
| ADR-018 API & event contracts | FastAPI generates OpenAPI 3.1. WebSocket event payloads are Pydantic discriminated unions included in the OpenAPI components. The TS client is generated with `openapi-typescript` + `openapi-fetch`. Events carry a schema `v`. CI fails on drift between the generated client and the server. | Hand-written TS types, GraphQL | API.md, NFR-MNT-001 |
| ADR-019 Secrets | `keyring` (Windows Credential Manager, macOS Keychain, Secret Service). On a headless Linux system with no backend, Panos refuses to store secrets unless you opt in to an encrypted file using a passphrase. Secrets never go into SQLite, logs or prompts. | Plaintext config / .env | NFR-SEC-003/004 |
| ADR-020 Audit log | Append-only table with a **hash chain** (each event hashes the previous one) for tamper evidence. Redaction happens before writing. Events are queryable via `/audit`. Retention is configurable. Diagnostic bundle export is redacted. | Plain log files only | FR-028, NFR-OBS-003 |
| ADR-021 Packaging | Python node frozen with **PyInstaller (onedir)** as a Tauri sidecar. Tauri bundler: MSI/NSIS (Windows), dmg (macOS, signed and notarized), AppImage/deb (Linux). Voice models are downloaded on first use with checksum verification, not bundled. Decided in detail at M7. | Nuitka (slower builds), requiring a system Python | MVP#3 |
| ADR-022 First production OS adapter | **Windows** is the first production-ready adapter (your development machine). macOS and Linux adapters are implemented for discovery, launch and open_path, and both have contract tests in CI from M3. Window metadata and capture on those platforms follow in M7. | — | MVP#1, BR-002 |
| ADR-023 Prompt-injection defense | Untrusted content goes into labeled envelopes (spotlighting/delimiting) and is never placed in system/developer roles. Policy decisions never depend on model claims. Approvals display the exact structured payload. A tool request does not inherit authority from content. A curated injection corpus runs in CI. | — | SECURITY, TOOLS (result handling), KNOWLEDGE_MEMORY rules |
| ADR-024 Default privacy & policy config | New and registered projects default to **LOCAL_ONLY** (fail-safe), switchable per project (audited). Default P2 behavior is **ask**. File deletion is P2 for single tracked files that git or a checkpoint can recover, and P3 for bulk deletes (>N files), untracked or ignored files, or anything outside recovery coverage. | Default STANDARD (cloud-eligible) | BR-004, PERMISSIONS |
| ADR-025 Screenshots | Captured in memory only and labeled LOCAL_ONLY by default (a screenshot may show anything). Analyzed by a local vision model. Cloud vision only with explicit per-request consent, when the active project policy allows it. Never persisted unless diagnostics are explicitly enabled. | — | BR-021, FR-015, NFR-PRV-002 |

Spike before committing to voice: **S-1, a time-boxed (≈1–2 day) throwaway prototype in the scratchpad.** It validates a custom "Panos" wake word, Greek and mixed STT, and Greek TTS on your hardware. The results feed ADR-016. No production code.

---

## 4. Proposed ID additions

### 4.1 NFR IDs for unnumbered NFRs, with proposed quantified targets

| ID | Requirement | Proposed target (for approval) |
|---|---|---|
| NFR-REL-001 | Persist job state before consequential steps | 100% of P2+ tool steps have a persisted step record before execution (enforced by tool runtime) |
| NFR-REL-002 | Safe recovery after runtime/UI crash | Interrupted jobs recover to PAUSED, with no duplicated consequential action |
| NFR-REL-003 | Idempotent tool calls where feasible | Each tool declares `idempotent: bool`; non-idempotent tools are not auto-retried |
| NFR-REL-004 | Failed tasks never reported as successful | Completion requires all verification steps to have passed or been reported as skipped |
| NFR-PERF-001 | Wake word feels immediate | ≤ 300 ms from end of wake word to visual feedback on reference hardware |
| NFR-PERF-002 | Simple local commands need no cloud round trip | "open X", "list projects", "pause" handled locally, < 1.5 s end-to-end from text input |
| NFR-PERF-003 | UI stays responsive | No main-thread block > 100 ms. Local API read p95 < 100 ms |
| NFR-PERF-004 | Incremental indexing | Re-index touches only changed files (content hash) |
| NFR-PORT-001 | OS-neutral core | Enforced by import-linter: no `platform.*` adapter imports from domain |
| NFR-PORT-002 | OS behavior behind adapter contracts | One contract test suite run against each adapter in a CI matrix |
| NFR-PORT-003 | Pluggable AI/STT/TTS/vector/integration providers | Ports + adapters; domain imports no SDK (import-linter) |
| NFR-MNT-001 | Typed interfaces & versioned schemas | mypy `--strict` on core; TS `strict`; versioned API/events |
| NFR-MNT-002 | High coverage for policy/permission/routing | ≥ 95% branch coverage on policy, approvals, routing, privacy, state machine; ≥ 80% line coverage overall for core |
| NFR-MNT-003 | ADRs for major decisions | `docs/adr/` |
| NFR-MNT-004 | No provider-specific behavior in domain services | import-linter contract |
| NFR-OBS-001 | Structured logs with correlation/job/step IDs | JSON logs via structlog contextvars |
| NFR-OBS-002 | Metrics (latency, failures, tokens/cost, tool outcomes, queue depth) | In-process metrics registry exposed at `/api/v1/metrics` (local) |
| NFR-OBS-003 | Redacted diagnostic bundle | `panos diag bundle` produces a zip with redacted logs/config/audit excerpt |
| NFR-ACC-001 | Core functionality usable without voice | All features reachable from CLI and desktop |
| NFR-ACC-002 | Keyboard-accessible controls | jsx-a11y lint plus axe checks in Playwright |
| NFR-ACC-003 | Voice output mutable independently | Separate TTS mute versus wake-word mute |

### 4.2 AC numbering (in document order)

AC-US001-01 works offline · -02 ambient audio never goes to the cloud · -03 starts an utterance session with visible feedback
AC-US012-01 detects languages/frameworks/build/test · -02 snapshot recorded before modification · -03 critical uncertainty triggers clarification
AC-US020-01 plan · -02 checkpoint · -03 format/lint/build/tests run · -04 report of files/tests/issues
AC-US031-01 cascade · -02 exclusions · -03 record version/decision/timestamp · -04 modification creates a new version that needs reapproval
AC-US040-01 diff/summary shown · -02 exact-action approval required
AC-US041-01 inspect without approval · -02 execution enters WAITING_FOR_APPROVAL · -03 rejection means nothing executes
AC-US043-01 read/search/summarize/draft · -02 no send tool in registry · -03 injection cannot cause a send
AC-US050-01 state survives UI restart · -02 limits on runtime/cost/tokens/repairs · -03 notification on approval/blocker transitions
AC-US061-01 only authenticated authorized nodes · -02 target node visible before approval · -03 target-node permissions apply

### 4.3 Proposed new user stories (to cover BRs with no story)

- **US-005** I can mark a project or folder local-only, and Panos never sends its content or derived context to cloud AI. (BR-004, NFR-SEC-005)
- **US-024** I can use voice, coding, requirements and repository analysis without internet when local models are installed. (BR-027)
- **US-044** Panos shows calendar conflicts and asks me before creating, changing or responding to events. (BR-020, FR-018)
- **US-045** Panos can research with a browser but never submits forms or uploads without me. (BR-022, FR-016)

ACs for the 17 existing stories without them, and for these 4 new ones, are drafted in M1-00 for your approval.

---

## 5. Implementation backlog

Legend: **Size** S ≤ 1 day, M 2–3 days, L ≈ 1 week (indicative, agent-assisted). Every item delivers tests tagged with requirement IDs via `@pytest.mark.req(...)` / Vitest `describe("[FR-011] …")`, and those tags feed the coverage report (M1-08).

### M0 — Pre-work (documentation only, no production code)

| ID | Item | Traces | Evidence | Size |
|---|---|---|---|---|
| M0-01 | Resolve decisions D-1..D-10 (§12); move specs to `docs/specs/` if approved | I-8 | Updated README links | S |
| M0-02 | Write and accept ADR-001..025 | NFR-MNT-003 | `docs/adr/*` | M |
| M0-03 | Draft missing ACs and new user stories US-005/024/044/045 | G-2, G-3 | `docs/specs/ACCEPTANCE_CRITERIA.md`, `USER_STORIES.md` | S |
| M0-04 | Seed traceability matrix `docs/traceability/requirements.yaml` with all BR/FR/NFR/US/AC IDs | FR-009 (for Panos itself) | YAML + validation script | S |
| S-1 | Voice feasibility spike (throwaway, scratchpad) | BR-005, R-3/R-4 | Short findings note → ADR-016 | S–M ✅ done: `docs/planning/S-1_voice_feasibility.md` |

### M1 — Skeleton

| ID | Item | Traces | Evidence | Size |
|---|---|---|---|---|
| M1-01 | Monorepo scaffold: uv workspace, pnpm workspace, ruff, mypy strict, import-linter contracts, pre-commit config, `.editorconfig`, GitHub Actions matrix (windows/macos/ubuntu × py3.12/3.13) | NFR-MNT-001, NFR-PORT-001, BR-002 | CI green on 3 OSes | M |
| M1-02 | Configuration: pydantic-settings, layered (defaults → user config TOML → env), `platformdirs` data/config/log/runtime dirs, workspace `~/Panos/Projects` created on first run | BR-029, FR-004 | Unit tests per OS path rules | S |
| M1-03 | Domain entities and enums: Project (privacy_mode, preferred_ide, ai_policy), Job (all 11 FR-022 states, budget), JobStep, ActionApproval, AuditEvent, Node (local self-identity), Checkpoint | FR-004, FR-010, FR-022, FR-028, DATA_MODEL | Domain unit tests | M |
| M1-04 | Persistence: SQLAlchemy 2 async + aiosqlite (WAL, foreign keys on), Alembic baseline migration, repository ports and SQLite implementations, transaction/unit-of-work | DATA_MODEL, NFR-REL-001 | Integration tests on temp DB; migration up/down test | M |
| M1-05 | Structured logging: structlog JSON, contextvars correlation/job/step IDs, secret-redaction processor (initial pattern set) | NFR-OBS-001, NFR-SEC-004 | Unit tests: IDs propagate; secrets redacted | S |
| M1-06 | FastAPI app factory: `/api/v1/health`, `/api/v1/capabilities`, problem+json errors, loopback token auth + Host/Origin validation (ADR-003), OpenAPI export script | API.md, NFR-SEC-001 | API tests: 401 without token, 400 on foreign Host | M |
| M1-07 | CLI (Typer): `panos serve`, `panos status`, `panos config show/path`; daemon single-instance lock; token file | BR-001, MVP#2, ADR-002 | CLI tests via CliRunner + subprocess smoke | S |
| M1-08 | Test infrastructure: pytest, pytest-asyncio, hypothesis, pytest-cov thresholds, pytest-timeout, `req` marker plugin → `docs/traceability/coverage.md` generator | TEST_STRATEGY, FR-009 | Coverage report generated in CI | S |

**Exit:** `/api/v1/health` works (authenticated), core domain tests pass, CI is green on Windows/macOS/Linux.

### M2 — Policy and tools

| ID | Item | Traces | Evidence | Size |
|---|---|---|---|---|
| M2-01 | Tool contract (`ToolSpec`: name/version, typed I/O, action class, permission level, platforms, side effects, timeout, cancellable, idempotent, privacy implications, audit fields) and allowlisted registry with profiles. **The default profile structurally excludes email send.** | FR-011, TOOLS, NFR-SEC-002, AC-US043-02, BR-019 | Registry tests; test asserting no tool of class `email.send` is registerable | M |
| M2-02 | Policy engine (ADR-005): action classes, P0–P4, default YAML = PERMISSIONS table incl. P4 for browser submission/upload and email send, project overrides (tighten-only for P3/P4) | NFR-SEC-001, BR-017, BR-018, BR-022, PERMISSIONS | Table-driven and property-based tests; ≥95% branch coverage | M |
| M2-03 | Tool runtime pipeline: schema validate → target resolve → privacy check → policy → approval check → persist step → execute (timeout/cancel) → output validate → audit. **The only execution path.** | FR-011, FR-028, NFR-SEC-002, NFR-REL-001 | Tests proving every path goes through policy (registry rejects unwrapped execution) | M |
| M2-04 | Approval service (ADR-006): JCS hash, HMAC token, expiry, single-use, invalidation on payload change, `GET /approvals`, `POST /approvals/{id}/decision` | FR-010, PERMISSIONS, AC-US040-02, AC-US041-02, AC-US041-03 | Forged, replayed, expired and mutated-payload tests | M |
| M2-05 | Core job state machine (transition table, persisted transitions, audit), pulled forward from M5 | FR-022, NFR-REL-001 | Exhaustive transition tests | S |
| M2-06 | Filesystem tools: list/read/search/create/update/move/delete. Project-scoped, realpath and symlink/junction escape checks, Windows specifics (drive-relative paths, UNC, ADS `:`, reserved names, case-insensitivity, long paths), atomic writes, size limits, delete impact classification (ADR-024) | FR-004, PERMISSIONS, TOOLS | Path-traversal security tests per OS | M |
| M2-07 | Controlled shell tool (ADR-007): argv execution, pwsh/powershell/bash/zsh/WSL profiles, bashlex and PowerShell-AST classifiers, env scrubbing, cwd confinement, timeouts, process-tree kill, capped streaming output, metadata (exit code, duration, argv, cwd) | FR-012, FR-013, FR-014, BR-018, NFR-SEC-002 | Bypass tests: `git commit`, `docker run`, `kubectl apply`, `pwsh -EncodedCommand`, `bash -c`, `;`/`&&` chains, `cmd /c` → classified P3 or denied | L |
| M2-08 | Audit service (ADR-020): append-only, hash chain, redaction, `GET /audit` with filters | FR-028, NFR-SEC-004 | Tamper-detection test; redaction test | S |
| M2-09 | Secret detection/redaction library (patterns + entropy), shared by logging, audit and prompts | NFR-SEC-004 | Corpus-based tests | S |
| M2-10 | Secret store (ADR-019) via keyring; node HMAC key generated and stored there | NFR-SEC-003 | Tests with a keyring test backend | S |
| M2-11 | Security suite v1: approval bypass via alternative tool, path traversal, shell argument injection, forged/replayed approval | TEST_STRATEGY security | `tests/security/` green | M |

**Exit:** forbidden and approval-required actions cannot bypass policy (proved by the security suite).

### M3 — Projects and repository intelligence

| ID | Item | Traces | Evidence | Size |
|---|---|---|---|---|
| M3-01 | Project registry: create in workspace / register any path, privacy mode (default LOCAL_ONLY), preferred IDE, AI policy, knowledge settings; `GET/POST /projects`, `GET /projects/{id}` | FR-004, BR-029, BR-004, US-011, US-005 | API and domain tests | M |
| M3-02 | Stack/build/test detectors (plugin per ecosystem): Python (pyproject/requirements/Django/FastAPI), Node (package.json, NestJS, React/TS), Java (Maven/Gradle, Spring Boot, Vaadin), PHP (composer, Laravel), Docker/Compose/K8s manifests, DB hints (PostgreSQL/MongoDB), cloud IaC hints | FR-005, BR-008, BR-011, AC-US012-01 | Fixture repos per stack in `tests/fixtures/repos/` | L |
| M3-03 | Repository snapshot/map: file/module map (tree-sitter symbol outline where supported), dependency inventory, conventions (editorconfig/linters/formatters), docs inventory, git state/history summary, confidence + "needs clarification" flags; persisted `RepositorySnapshot`; `POST /projects/{id}/analyze` | FR-005, BR-011, US-012, AC-US012-02, AC-US012-03 | Snapshot tests on fixtures; low-confidence fixture yields clarification | L |
| M3-04 | Git read tools via git CLI (porcelain v2 parsers): status, diff, log, branches, show, blame | FR-013, BR-017 | Tests on sandbox repos | M |
| M3-05 | Git local branch create (P1, not shared history) | FR-013, TOOLS | Tests | S |
| M3-06 | OS adapter contract + **Windows production adapter** (list_installed_apps, launch_app, open_path, focus_window, active_window_metadata). macOS/Linux adapters for discovery/launch/open_path; contract tests in CI per OS | BR-002, BR-006, FR-003, NFR-PORT-002, US-010 | Contract suite green on 3 OSes (non-implemented capabilities report `unsupported`, never crash) | L |
| M3-07 | Application registry: discovery of JetBrains products (incl. Toolbox), VS Code, Sublime, installed browsers; aliases; preferred IDE per project/technology; "open project in IDE" | FR-003, BR-006, US-010 | Tests with fake adapter + Windows real-machine check | M |
| M3-08 | CLI: `panos project add/list/show/analyze/open`, `panos apps list/launch` | MVP#2 | E2E CLI test on fixture | S |
| M3-09 | Basic local knowledge index (lexical): FTS5, incremental by hash, `.gitignore`-aware, privacy label per source, provenance | FR-019 (partial), FR-020, NFR-PRV-003, NFR-PERF-004, MVP#17 | Indexing and incremental tests | M |

**Exit:** the CLI can register, analyze and open a real project in the preferred IDE (on Windows).

### M4 — AI layer

| ID | Item | Traces | Evidence | Size |
|---|---|---|---|---|
| M4-01 | Provider ports (chat with streaming + tool calls, embeddings, vision); neutral message/tool types | BR-003, NFR-PORT-003, NFR-MNT-004 | import-linter: domain doesn't import SDKs | M |
| M4-02 | Adapters: OpenAI-compatible (Ollama/llama.cpp/LM Studio/vLLM), Anthropic, OpenAI, scripted mock; locality verification (ADR-009) | BR-003, FR-021 | respx-mocked adapter tests; locality tests (remote host ≠ local) | L |
| M4-03 | Model capability registry (YAML): tier, context, tool-use, vision, languages, cost, privacy eligibility; health checks; provider config via settings and keyring | FR-021, DATA_MODEL ModelProvider/Model | Unit tests | S |
| M4-04 | Router: hard filters (privacy, offline, capability, context fit, health) → scoring (task tier, complexity, latency, cost/budget) → explanation record; budget escalation goes through approval or cheaper fallback | FR-021, FR-023, BR-003, BR-027, NFR-PRV-004, NFR-PERF-002 | Table and property tests; ≥95% branch coverage | M |
| M4-05 | Privacy labels and taint propagation (ADR-008); egress guard in every cloud adapter; secret redaction before cloud calls | BR-004, NFR-SEC-005, NFR-SEC-004, US-005 | **Property-based test: for any context containing a LOCAL_ONLY item, no cloud adapter call occurs; network-spy e2e** | M |
| M4-06 | Prompt/context builder: role separation, untrusted envelopes (ADR-023), retrieval priority order (project → company → past → user → external → general), provenance citations, token budgeting | FR-020, SECURITY, KNOWLEDGE_MEMORY, TOOLS | Snapshot tests of assembled prompts; injection corpus v1 | M |
| M4-07 | Embeddings (fastembed, local) + sqlite-vec + hybrid RRF retrieval | FR-020, MVP#17 | Retrieval quality smoke on fixture (el/en) | M |
| M4-08 | AI usage/cost metering per call/job/project | FR-023, NFR-OBS-002 | Unit tests | S |
| M4-09 | Conversation: `POST /conversation/messages`, token streaming over `/ws/events`, local intent classification (direct answer / simple local command / job) | FR-002, BR-001, NFR-PERF-002 | API + WS tests | M |
| M4-10 | `panos ask "…"` CLI and offline-mode tests | BR-027, US-024 | Offline e2e (network disabled) | S |

**Exit:** tests prove local-only content cannot route to cloud (unit, property and e2e).

### M5 — Orchestration and coding

| ID | Item | Traces | Evidence | Size |
|---|---|---|---|---|
| M5-01 | Job runner: asyncio workers, step persistence before consequential steps, crash recovery to PAUSED, pause/resume/cancel with cancellation propagated into tools; `/jobs` endpoints + `/jobs/{id}/events` | FR-022, FR-002, US-052, NFR-REL-001..004, AC-US050-01 | Kill-the-process recovery test; cancel kills child process tree | L |
| M5-02 | Limits: max runtime, cost, tokens, repair attempts → BLOCKED/WAITING_FOR_USER | FR-023, AC-US050-02 | Unit + integration | S |
| M5-03 | Agent role framework (scoped context, permissions, objective → structured result; handoff preserves IDs/decisions/assumptions/provenance); Planner, Developer, Reviewer, QA roles | AGENTS, BR-007 | Contract tests with scripted model | M |
| M5-04 | Checkpoint manager (shadow git, ADR-012) + rollback (`POST /jobs/{id}/rollback`, `panos job rollback`) | FR-024, US-023, AC-US020-02 | Rollback restores exact bytes; non-git project test | M |
| M5-05 | Coding workflow: understand → analyze (requires fresh snapshot) → plan → checkpoint → structured edits → format/lint/type/build/test (discovered commands) → review → trace check → report | FR-006, US-020, AC-US020-01..04, BR-011 | Fixture-repo e2e with scripted model | L |
| M5-06 | Bounded repair loop (diagnose failure → fix → re-run; max N, then BLOCKED with report) | US-022, FR-023 | Tests: stops at limit, never reports success on failure (NFR-REL-004) | M |
| M5-07 | Git commit approval flow: diff summary + staged tree hash + message in the approval payload; execute only if the tree hash still matches | BR-017, FR-013, US-040, AC-US040-01/02 | Change-after-approval test invalidates; no auto-commit | M |
| M5-08 | Docker flow: inspection P0 (Dockerfile/compose parsing, `docker compose config`, `docker ps`), execution P3 via approval | BR-018, FR-014, US-041, AC-US041-01..03 | Rejected → zero docker processes spawned (spy) | M |
| M5-09 | Notification service: domain events → channels (CLI/console now, desktop in M7, voice in M7); only approvals, blockers and completion are notified | FR-025, US-051, AC-US050-03 | Unit tests | S |
| M5-10 | E2E: CLI → open project → analyze → implement fixture feature → tests → commit approval requested (not executed) | TEST_STRATEGY e2e#1 | e2e green | M |

**Exit:** a fixture-repository feature is implemented end-to-end without auto-commit.

### M6 — Requirements

| ID | Item | Traces | Evidence | Size |
|---|---|---|---|---|
| M6-01 | Requirement entities and versioning: types BR/FR/NFR/EPIC/FEAT/US/AC/TC/API/ADR/RISK/ASSUMPTION…; stable ID allocation; lifecycle (ADR-014) | FR-008, BR-009 | Unit tests | M |
| M6-02 | Hierarchical approvals: cascade, exclusions, per-descendant record, new version on edit, reapproval | BR-010, FR-010, US-031, AC-US031-01..04 | Full AC test suite | M |
| M6-03 | Trace links, traceability query `GET /projects/{id}/traceability`, change impact (stale marking + impact-analysis task) | FR-009, US-032, REQUIREMENTS_PIPELINE | Graph tests | M |
| M6-04 | Brief → ambiguity analysis → interview (persisted Q/A state, resumable) | FR-007(1–4), US-030 | Scripted-model tests | M |
| M6-05 | Markdown artifact generators (Jinja2): BRD, FRD, NFR, architecture, ERD (Mermaid), OpenAPI skeleton, backlog, ACs, test cases, estimates, sprint plan, risks/assumptions | FR-008, FR-029 (MD), BR-028 (MD) | Golden-file tests | L |
| M6-06 | Stack proposal + mandatory approval before scaffolding; stack knowledge packs for the 8 families as declarative data | BR-008, US-021, FR-007(5–6), CLAUDE.md | Explicit-stack-honored test | M |
| M6-07 | Template-based offline scaffolding (official generators optional when online and approved); open in preferred IDE | FR-007(7, 11) | Scaffold tests per pack (structure-level) | L |
| M6-08 | Initial estimation: story points, hours, person-days, role rates config, cost, confidence | BR-014 | Unit tests | S |
| M6-09 | Claude skills generator: from approved facts only; versioned; regenerate when approved architecture or standards change; written to `.claude/skills/<name>/SKILL.md` in the generated project | BR-013, FR-030, US-033 | Tests: unapproved facts excluded; version bump on change | M |
| M6-10 | Requirements API endpoints | API.md | API tests | S |
| M6-11 | E2E: brief → interview → approved spec/backlog → scaffold → skills → open IDE | TEST_STRATEGY e2e#2 | e2e green | M |

**Exit:** a brief can become an approved spec and backlog plus a generated project skeleton.

### M7 — Desktop and voice

| ID | Item | Traces | Evidence | Size |
|---|---|---|---|---|
| M7-01 | Tauri 2 shell: sidecar start/attach (ADR-002), token handoff, single instance, tray, autostart, minimal capabilities | ADR-015, MVP#3 | Rust unit tests; manual check on Windows | M |
| M7-02 | Generated TS client + WS event types; drift check in CI | ADR-018, NFR-MNT-001 | CI step | S |
| M7-03 | React UI: conversation (streaming), jobs/steps timeline with pause/resume/cancel/rollback, approvals with diff/payload view, projects, apps, settings (providers, budgets, privacy), audit viewer, local status; el/en i18n; keyboard access | BR-001, FR-027 (local), US-052, NFR-ACC-001/002, NFR-PERF-003 | Vitest + RTL; Playwright against mock API; axe | L |
| M7-04 | WebSocket event bus (server fan-out, reconnect + resync) | API.md | Integration tests | M |
| M7-05 | Global hotkey / push-to-talk (Tauri global-shortcut) | FR-001, US-002 | Manual + unit | S |
| M7-06 | Desktop notifications channel (Tauri notification) + optional voice notification | FR-025, US-051, AC-US050-03 | Integration | S |
| M7-07 | Voice worker (ADR-016): hotkey/push-to-talk primary trigger, openWakeWord detector (dev wake word) + sensitivity + mute, VAD, STT (faster-whisper `small`, Greek default + vocabulary prompt, accurate mode), Piper TTS with language-run splitter, barge-in; command routing (`stop talking` vs `stop/pause/resume`); in-memory audio only | BR-005, FR-001, FR-002, US-001, US-003, US-004, AC-US001-01..03, NFR-PRV-001, NFR-PERF-001, NFR-ACC-003 | Adapter tests with recorded fixtures (test-only); no-network test for wake word; no audio files written test | L |
| M7-07a | Train and validate custom "Panos" / "Hey Panos" wake-word model (WSL2 or one-off cloud GPU), ≥ 20 real recordings, false-accepts/hour measurement; enable by default only if targets met | BR-005, US-001, AC-US001-01..03 | Validation report | M |
| M7-08 | Active-window capture + analysis (ADR-025): Windows production; macOS (permission flow); Linux X11, Wayland via portal (degraded) | BR-021, FR-015, US-013, NFR-PRV-002 | No-persistence test; local-vision routing test | M |
| M7-09 | Windows packaging (PyInstaller sidecar + Tauri MSI/NSIS); macOS/Linux bundles best-effort | ADR-021 | Installer smoke on Windows | M |

**Exit (= MVP):** you can operate the MVP primarily through desktop and voice.

### M8 — Integrations, research and documents (decomposed 2026-10-09)

**Owner decisions (2026-10-09):**
- Mail and calendar: **Gmail / Google Calendar** first. Microsoft 365 mail, Outlook COM and Thunderbird are deferred.
- Trackers: **Jira Cloud and Azure DevOps**.
- Browser: **Playwright Chromium** with an isolated profile.
- Cloud document sources: **Google Drive, OneDrive/SharePoint, selected websites**. Local Office/PDF files and Git history are always included.

| ID | Item | Traces | Evidence | Size |
|---|---|---|---|---|
| M8-00 | Connector foundation (ADR-026). **Accounts:** connector accounts (migration 0007) with tokens in the OS keyring only. **Auth:** Google installed-app OAuth (loopback + PKCE); Microsoft device-code flow; Jira API token; Azure DevOps PAT. **HTTP:** a per-connector endpoint allowlist (method + path) enforced in the HTTP client; retries and rate limits. **Privacy:** connector content is labelled untrusted and LOCAL_ONLY by default. **Surfaces:** `panos connect …` and `/connectors` API | BR-019, BR-020, BR-016, NFR-SEC-002/004, NFR-PRV-003 | Unit tests with mocked HTTP; allowlist tests; no token in DB/logs/audit | L |
| M8-01 | Gmail: search, read, thread summary, **draft only** (`drafts.create`/`update`). The send endpoints are absent from the allowlist and no `email.send` capability exists. Prompt-injection tests (mail asking to send/forward/delete) | BR-019, FR-017, US-043, AC-US043-01..03 | Mocked Gmail API; injection corpus; registry test | M |
| M8-02 | Google Calendar: events, free/busy, conflict detection. Create/update/delete/respond are prepared as an exact operation and executed only after P3 approval | BR-020, FR-018, US-044, AC-US044-01/02 | Mocked API; approval-binding tests | M |
| M8-03 | Browser research (ADR-027): Playwright Chromium, isolated profile, GET navigation only. Tools `browser.read` (text, title, links, citation) and `browser.search`. A research workflow reads N sources and answers with citations. No submit/upload tool is registered; page content is untrusted | BR-022, FR-016, US-045, AC-US045-01..03 | Local test pages (forms, injection); no-submit test | M |
| M8-04 | Knowledge adapters: DOCX/XLSX/PPTX/PDF extraction in the indexer; Git history (commit messages and changed files); knowledge collections outside projects (company folders); selected websites (robots.txt, size limits); Google Drive and OneDrive/SharePoint via connectors. Provenance per chunk; ranking per FR-020 | BR-012, FR-019, FR-020 | Fixture documents; mocked Drive/Graph; ranking test | L |
| M8-05 | Document exports: requirement documents to DOCX and PDF, backlog/estimates to XLSX, a summary deck to PPTX; Greek text supported | BR-028, FR-029 | Golden structure tests (open and inspect the generated files) | M |
| M8-06 | Jira Cloud and Azure DevOps: read and search work items. Prepared change sets (create/update/transition/comment) need P3 approval of the exact set; then they are executed and linked as requirement evidence | BR-016, US-042, AC-US042-01/02 | Mocked APIs; approval-binding tests | M |
| M8-07 | Surfaces: API, CLI and desktop (Connections in Settings; mail/calendar/research in chat; exports in Projects); conversation intents (Greek/English) | BR-001, FR-002 | API tests; Vitest + axe | M |
| M8-08 | End-to-end and documentation: ADR-026/027 notes, README, owner setup guide for the Google OAuth client, Microsoft app registration, Jira token and ADO PAT | — | E2E with mocked providers; live checks by the owner | S |

**Out of M8:** Microsoft 365 mail/calendar, Outlook COM, Thunderbird; browser form submission (stays P4, not implemented).

### M9–M10 — Post-MVP (epic level; M8 decomposed above)

| ID | Epic | Traces |
|---|---|---|
| M8-01 | Email read/search/summarize/draft adapters (M365 Graph without Mail.Send, Gmail with endpoint allowlist, Thunderbird local mbox/maildir read); injection tests | BR-019, FR-017, US-043, AC-US043-01..03 |
| M8-02 | Calendar read/free-busy/conflicts + approval-gated writes | BR-020, FR-018, US-044 |
| M8-03 | Browser research tool (read/navigate/extract; P4 submission/upload) | BR-022, FR-016, US-045 |
| M8-04 | Knowledge adapters: Office/PDF docs, Git history, company folders, SharePoint/OneDrive/Google Drive, selected websites | BR-012, FR-019 |
| M8-05 | Document exports DOCX/XLSX/PPTX/PDF | BR-028, FR-029 |
| M8-06 | Jira/Azure DevOps read + prepared changes + approval-gated writes | BR-016, US-042 |
| M9-01..04 | Control plane (PostgreSQL), node enrollment + mTLS identity + revocation, remote dispatch, React web dashboard | BR-024, BR-025, FR-026, FR-027, NFR-SEC-006, US-060..062, AC-US061-01..03 |
| M10-01..03 | Mobile companion, estimate-vs-actual learning, model/cost analytics | BR-026, BR-015, BR-014 |

---

## 6. Requirement coverage matrix (reverse mapping)

**Business requirements**

| ID | Backlog items | MVP? |
|---|---|---|
| BR-001 | M1-07, M4-09, M7-03 | ✔ |
| BR-002 | M1-01, M3-06, M7-08 | ✔ |
| BR-003 | M4-01..04 | ✔ |
| BR-004 | M3-01, M4-05, ADR-008/024 | ✔ |
| BR-005 | M7-05, M7-07 | ✔ |
| BR-006 | M3-06, M3-07 | ✔ |
| BR-007 | M5-03, M5-05, M5-06 | ✔ |
| BR-008 | M3-02, M6-06, M6-07 | ✔ |
| BR-009 | M6-01..05 | ✔ |
| BR-010 | M6-02 | ✔ |
| BR-011 | M3-03, M5-05 | ✔ |
| BR-012 | M3-09, M4-06, M4-07 (basic); M8-04 | partial |
| BR-013 | M6-09 | ✔ |
| BR-014 | M6-08; M10 | partial |
| BR-015 | M10-02 | later |
| BR-016 | M8-06 | later |
| BR-017 | M3-04, M3-05, M5-07 | ✔ |
| BR-018 | M2-07, M5-08 | ✔ |
| BR-019 | M2-01 (no-send invariant); M8-01 | invariant ✔ / feature later |
| BR-020 | M8-02 | later |
| BR-021 | M7-08 | ✔ |
| BR-022 | M2-02 (P4 policy); M8-03 | policy ✔ / feature later |
| BR-023 | M5-01, M5-02, M5-09 | ✔ |
| BR-024 | M9 | later |
| BR-025 | M7-03 (local status); M9 | partial |
| BR-026 | M10 | later |
| BR-027 | M4-04, M4-10, M7-07 | ✔ |
| BR-028 | M6-05 (Markdown); M8-05 | partial |
| BR-029 | M1-02, M3-01 | ✔ |

**Functional requirements:** FR-001 M7-05/07 · FR-002 M4-09, M5-01, M7-07 · FR-003 M3-06/07 · FR-004 M1-02, M3-01 · FR-005 M3-02/03 · FR-006 M5-05 · FR-007 M6-04..07, M6-11 · FR-008 M6-01, M6-05 · FR-009 M6-03 · FR-010 M2-04, M6-02 · FR-011 M2-01..03 · FR-012 M2-07 · FR-013 M3-04/05, M5-07 · FR-014 M2-07, M5-08 · FR-015 M7-08 · FR-016 M8-03 · FR-017 M8-01 · FR-018 M8-02 · FR-019 M3-09, M8-04 · FR-020 M4-06/07 · FR-021 M4-04 · FR-022 M2-05, M5-01 · FR-023 M4-04, M4-08, M5-02, M5-06 · FR-024 M5-04 · FR-025 M5-09, M7-06 · FR-026 M9 · FR-027 M7-03, M9 · FR-028 M2-08 · FR-029 M6-05, M8-05 · FR-030 M6-09

**NFRs:** SEC-001 M1-06, M2-02 · SEC-002 M2-01/03/07, ADR-013 · SEC-003 M2-10 · SEC-004 M1-05, M2-09, M4-05 · SEC-005 M4-05 · SEC-006 M9 · PRV-001 M7-07 · PRV-002 M7-08 · PRV-003 M3-09, M4-07 · PRV-004 M4-04 · REL-001..004 M2-03, M2-05, M5-01, M5-06 · PERF-001 M7-07 · PERF-002 M4-04, M4-09 · PERF-003 M7-03 · PERF-004 M3-09 · PORT-001..003 M1-01, M3-06, M4-01 · MNT-001..004 M1-01, M2-02, M4-04, M0-02 · OBS-001 M1-05 · OBS-002 M4-08 (+ metrics endpoint in M5-01) · OBS-003 M2-08 (diag bundle) · ACC-001..003 M7-03, M7-07

**User stories:** US-001 M7-07 · US-002 M7-05 · US-003 M7-07 · US-004 M7-07 · US-005* M3-01, M4-05 · US-010 M3-06/07 · US-011 M3-01 · US-012 M3-03 · US-013 M7-08 · US-020 M5-05 · US-021 M6-06 · US-022 M5-06 · US-023 M5-04 · US-024* M4-10 · US-030 M6-04 · US-031 M6-02 · US-032 M6-03 · US-033 M6-09 · US-040 M5-07 · US-041 M5-08 · US-042 M8-06 · US-043 M2-01, M8-01 · US-044* M8-02 · US-045* M8-03 · US-050 M5-01/02 · US-051 M5-09, M7-06 · US-052 M5-01, M7-03 · US-060..062 M9
(* = proposed new story)

**Acceptance criteria:** AC-US001-01..03 M7-07 · AC-US012-01..03 M3-02/03 · AC-US020-01..04 M5-04/05 · AC-US031-01..04 M6-02 · AC-US040-01..02 M2-04, M5-07 · AC-US041-01..03 M2-04, M5-08 · AC-US043-02 M2-01 (MVP), -01/-03 M8-01 · AC-US050-01..03 M5-01/02/09 · AC-US061-* M9

---

## 7. Proposed monorepo structure

This follows REPOSITORY_STRUCTURE.md, with two deviations, both justified:
- (a) Python packages use a **src layout under `packages/`**, so that `panos-core` cannot accidentally depend on FastAPI and each package declares its own dependencies.
- (b) Persistence and adapter infrastructure get explicit folders, and import-linter enforces the boundaries.

```text
panos/
├── CLAUDE.md
├── README.md
├── pyproject.toml              # uv workspace root + ruff/mypy/pytest/import-linter config
├── uv.lock
├── package.json                # pnpm workspace root scripts
├── pnpm-workspace.yaml
├── pnpm-lock.yaml
├── justfile                    # task runner (lint, test, gen-client, trace-report)
├── .editorconfig  .gitignore  .gitattributes  .pre-commit-config.yaml
├── .github/workflows/          # ci.yml (3-OS matrix), security.yml (pip-audit, pnpm audit)
├── docs/
│   ├── specs/                  # the 22 spec docs moved here (D-7)
│   ├── adr/                    # ADR-001..
│   ├── planning/               # this proposal, milestone reports
│   └── traceability/           # requirements.yaml, coverage.md (generated)
├── apps/
│   ├── desktop/
│   │   ├── src/                # React/TS (features/, components/, i18n/, lib/)
│   │   ├── src-tauri/          # Rust: main.rs, sidecar.rs, capabilities/, tauri.conf.json
│   │   └── package.json
│   ├── web/                    # M9
│   └── mobile/                 # M10
├── services/
│   ├── node-api/
│   │   ├── pyproject.toml
│   │   └── src/panos_node_api/ # app factory, routers/, ws/, auth/, deps.py (DI wiring)
│   └── control-plane/          # M9
├── packages/
│   ├── panos-core/
│   │   ├── pyproject.toml      # extras: [voice] [anthropic] [openai] [windows] [macos] [linux]
│   │   └── src/panos_core/
│   │       ├── domain/         # entities, value objects, enums, domain events (pure)
│   │       ├── conversation/  intent/
│   │       ├── orchestration/  agents/  jobs/  checkpoints/
│   │       ├── tools/          # contract, registry, runtime, builtin/{fs,shell,git,apps,screen,docker}
│   │       ├── policy/         # engine, action classes, defaults/*.yaml
│   │       ├── approvals/  audit/  secrets/  redaction/
│   │       ├── ai/             # ports, router, registry, privacy/, context/, adapters/{openai_compat,anthropic,openai,mock}
│   │       ├── knowledge/      # ports, indexer, retrieval, adapters/{fts5,sqlite_vec,fastembed}
│   │       ├── voice/          # ports, command routing, adapters/{openwakeword,silero,faster_whisper,piper,os_tts}
│   │       ├── projects/  repository/   # registry, detectors/, snapshot, map
│   │       ├── requirements/  traceability/  estimation/  skills/  documents/
│   │       ├── notifications/  nodes/  integrations/   # (M8+)
│   │       ├── persistence/    # SQLAlchemy models, repositories, UoW
│   │       ├── config/  observability/
│   │       └── platform/       # ports.py + windows/ macos/ linux/ + fake/
│   ├── panos-cli/
│   │   └── src/panos_cli/      # Typer app; thin HTTP client to node-api
│   └── ts-client/              # generated OpenAPI types + fetch client
├── tests/
│   ├── unit/  integration/  e2e/  security/
│   ├── contract/               # OS adapter + AI adapter contract suites
│   └── fixtures/               # repos/ (per stack), prompts/injection/, audio/ (test-only)
└── scripts/                    # export_openapi.py, gen_ts_client, trace_report.py, bootstrap
```

**import-linter contracts:**
- `domain` imports nothing from Panos infra, FastAPI, SQLAlchemy or provider SDKs.
- `platform.windows|macos|linux` are imported only by `platform` factories.
- `ai.adapters.*` are imported only by the AI composition root.
- `panos_node_api` → `panos_core` (never the reverse).

---

## 8. Libraries and rationale

### Python (≥ 3.12; CI on 3.12 and 3.13)

| Concern | Library | Why |
|---|---|---|
| Env/workspace | **uv** | Fast, lockfile, workspaces, manages Python versions |
| API | **FastAPI**, **uvicorn[standard]**, **pydantic v2**, **pydantic-settings** | Mandated by spec. OpenAPI generation, typed models, WebSockets |
| DB | **SQLAlchemy 2.0** (async) + **aiosqlite** + **Alembic** | Mature, typed, migrations, PostgreSQL-ready for the control plane |
| CLI | **Typer** + **Rich** | Typed CLI, good UX |
| HTTP | **httpx** | Async, used by all adapters, mockable with respx |
| Logging | **structlog** | Structured JSON, contextvars |
| Paths | **platformdirs**, **pathspec** | Correct per-OS dirs; `.gitignore` semantics |
| Processes | **psutil** | Cross-platform process-tree management |
| Shell parsing | **bashlex**; PowerShell AST via `pwsh -NoProfile` parser call | Accurate command classification (ADR-007) |
| Hashing | **rfc8785** | Canonical JSON for approval binding |
| Secrets | **keyring** | OS-backed secure storage |
| Templates | **Jinja2** | Artifacts, scaffolding, skills |
| Code map | **tree-sitter** + **tree-sitter-language-pack** | Multi-language symbol outline, prebuilt wheels |
| Knowledge | **sqlite-vec**, **fastembed** (ONNX) | Embedded, no server, no torch |
| AI SDKs (adapter extras only) | **anthropic**, **openai** | Official SDKs. OpenAI-compatible adapter uses httpx directly |
| Windows | **pywin32** (+ stdlib `winreg`) | Window APIs, ShellExecute, registry |
| macOS | **pyobjc** (AppKit, Quartz) | NSWorkspace, window list, capture |
| Linux | **python-xlib**, **jeepney** (D-Bus/portals) | X11 window info, xdg-desktop-portal |
| Capture | **mss** | Cross-platform fast screenshots (Windows/X11/macOS) |
| Voice (extra) | **sounddevice**, **openwakeword**, **onnxruntime** (Silero VAD), **faster-whisper**, **piper-tts** (optional, license check) | ADR-016 |
| Docs (M8) | python-docx, openpyxl, python-pptx; PDF engine chosen in M8 | BR-028 |
| Quality | **ruff** (lint+format, incl. bandit rules), **mypy --strict** (+ pydantic plugin), **import-linter**, **pip-audit** | Typing and architecture enforcement |
| Tests | **pytest**, **pytest-asyncio**, **hypothesis**, **pytest-cov**, **pytest-timeout**, **respx**, **time-machine** | Property tests for policy/routing; deterministic time for expiry |

Deliberately **not** used: LangChain/LangGraph/CrewAI (ADR-010), LiteLLM (ADR-009), Celery/Redis (ADR-011), GitPython (git CLI + porcelain parsing keeps execution on the same controlled path).

### TypeScript / React

| Concern | Library | Why |
|---|---|---|
| Tooling | **pnpm**, **TypeScript 5 (strict)**, **Vite** | Fast, standard with Tauri |
| UI | **React 19**, **Tailwind CSS**, **shadcn/ui** (Radix primitives) | Accessible primitives (NFR-ACC-002), components owned by us |
| Server state | **TanStack Query** | Caching, WS-driven invalidation |
| Routing | **TanStack Router** | Type-safe routes |
| Local state | **Zustand** | Minimal |
| API client | **openapi-typescript** + **openapi-fetch** | Generated from FastAPI OpenAPI (ADR-018) |
| Runtime validation | **zod** (WS messages at the boundary) | Defensive parsing of events |
| i18n | **i18next** + **react-i18next** | el/en |
| Content | **react-markdown** + **remark-gfm**, **mermaid**, **react-diff-view** | Artifacts, ERDs, approval diffs |
| Tests | **Vitest**, **React Testing Library**, **MSW**, **Playwright** + **@axe-core/playwright** | Unit, component, e2e, accessibility |
| Lint | **ESLint** (typescript-eslint, react-hooks, jsx-a11y), **Prettier** | Consistency, accessibility linting |

### Tauri (2.x)

Plugins: `global-shortcut`, `notification`, `single-instance`, `autostart`, `shell` (**sidecar only**, scoped), `window-state`, `log`, `opener`, and later `updater`. Rust code stays minimal (sidecar lifecycle, token handoff, tray). The capability files grant the WebView no fs or arbitrary-shell access.

---

## 9. Platform-specific functionality

Everything below sits behind `platform` ports (Python) or Tauri plugins (shell). Core logic stays OS-neutral.

| Capability | Windows (first, production) | macOS | Linux |
|---|---|---|---|
| App discovery | Registry Uninstall keys (HKLM/HKCU/WOW6432Node), App Paths, Start Menu `.lnk`, `shell:AppsFolder` (Store apps), JetBrains Toolbox dir, PATH launchers (`code`, `subl`) | `/Applications`, `~/Applications`, Spotlight `mdfind` bundle query, Toolbox scripts | XDG `.desktop` files (incl. Flatpak/Snap exports), PATH launchers, Toolbox scripts |
| App launch / open path | `ShellExecuteW` / exe with args; `os.startfile` | `open -a`, NSWorkspace | `gio launch` / parsed `Exec`, `xdg-open` |
| Focus window | `SetForegroundWindow` (foreground-lock workarounds) | NSRunningApplication activate (Accessibility for windows) | X11 `_NET_ACTIVE_WINDOW`; **Wayland: not generally possible** |
| Active window metadata | `GetForegroundWindow`, title, PID, exe | CGWindowList (**Screen Recording permission for titles**) | X11 via python-xlib; **Wayland: limited** (compositor-specific) |
| Capture active window | mss + window rect (DPI-aware); WGC later if occlusion matters | Quartz/ScreenCaptureKit (**Screen Recording permission**) | X11 mss; **Wayland: xdg-desktop-portal Screenshot (interactive)** |
| Global hotkey | Tauri global-shortcut | Tauri global-shortcut (may need Accessibility) | X11 OK; **Wayland: GlobalShortcuts portal where supported, else degraded** |
| Notifications | Tauri (WinRT toast; needs AUMID when installed) | Tauri (UNUserNotificationCenter, permission prompt) | Tauri (D-Bus org.freedesktop.Notifications) |
| Shells | PowerShell 5.1, pwsh 7, cmd (classification only), **WSL** (`wsl.exe -d <distro> --cd`, path translation) | zsh (default), bash | bash, zsh, sh |
| Process-tree kill | Job Objects / psutil | process groups (`killpg`) | process groups (`killpg`) |
| Secrets | Credential Manager | Keychain | Secret Service (gnome-keyring/KWallet); headless fallback per ADR-019 |
| Filesystem safety | Drive letters, UNC, junctions, ADS, reserved names, case-insensitive, long paths, file locks | Case-insensitive APFS default, symlinks, quarantine xattr | Case-sensitive, symlinks |
| Mic access | Windows privacy settings for desktop apps | TCC microphone prompt (Info.plist usage string) | PulseAudio/PipeWire |
| OS-native TTS | SAPI / WinRT (Greek voice installable) | AVSpeechSynthesizer (Greek "Melina") | speech-dispatcher / espeak-ng |
| Autostart | Run key (Tauri autostart) | LaunchAgent | XDG autostart `.desktop` |
| Packaging | MSI/NSIS, Authenticode signing | dmg, codesign + **notarization** | AppImage / deb |
| Runtime data dir | `%LOCALAPPDATA%\Panos` (outside OneDrive) | `~/Library/Application Support/Panos` | `$XDG_DATA_HOME/panos` |

---

## 10. MVP boundary and milestone dependencies

**MVP = M0 → M7**, which covers all 18 MVP.md capabilities, including basic local knowledge (moved into M3/M4).
**Out of MVP:** M8 (email, calendar, browser research, broad knowledge adapters, Office/PDF exports, Jira/ADO), M9 (control plane, multi-PC, web dashboard), M10 (mobile, learning).
Invariants that must hold from day one even though the features come later: no email-send tool (M2-01), P4 browser submission/upload (M2-02), and node identity in the data model (M1-03).

```text
M0 (ADRs, decisions, S-1 spike)
  └─► M1 Skeleton
        └─► M2 Policy & tools (+ core job FSM)
              └─► M3 Projects & repo intelligence (+ lexical index)
                    └─► M4 AI layer (+ vector/hybrid retrieval)
                          └─► M5 Orchestration & coding
                                ├─► M6 Requirements ───────────────┐
                                └─► M7 Desktop & voice  ◄──────────┘ (M7-03 requirement screens need M6 APIs)
                                      └─► [MVP]  ─► M8 Integrations ─► M9 Distributed ─► M10 Mobile/Optimization
```

Allowed parallelism (only if you want it): M7-01/02/04 (Tauri shell, client generation, WS bus) and the M7-07 voice worker can start once M5's job and approval APIs are frozen, alongside M6. Nothing visual or remote starts before the M2 security exit is met (IMPLEMENTATION_PLAN rule).

---

## 11. Per-milestone working protocol (after approval)

For each milestone:
1. List the requirement IDs in scope.
2. Write tests first or alongside the code, tagged with requirement IDs.
3. Implement in vertical slices.
4. Run `ruff format --check`, `ruff check`, `mypy --strict`, `lint-imports`, `pytest` (unit/integration/security/e2e as applicable), and from M7 also `pnpm lint`, `tsc --noEmit`, `vitest`, `playwright`, `cargo clippy`/`cargo test`.
5. Write a milestone report in `docs/planning/reports/M<n>.md`, covering requirement coverage (generated from test tags), changed files, test results, known limitations and documentation updates.
6. **Stop and wait for your approval** before the next milestone.
7. **No git commits** without your explicit approval.

---

## 12. Decisions needed from you

| # | Decision | My recommendation |
|---|---|---|
| D-1 | Move the repo out of OneDrive (e.g. `C:\dev\panos`)? | **Yes** (R-9) |
| D-2 | Windows as the first production OS adapter? | **Yes** |
| D-3 | Default privacy mode for new/registered projects | **LOCAL_ONLY** (fail-safe) |
| D-4 | Reference hardware (GPU/VRAM, RAM) and local LLM runtime | **Ollama** as default local runtime via the OpenAI-compatible adapter; tell me your GPU/VRAM |
| D-5 | Cloud providers to implement first | **Anthropic + OpenAI** (and any OpenAI-compatible) |
| D-6 | Usage: personal/internal or distributed product? (licensing of voice components) | Need your answer |
| D-7 | Move specs into `docs/specs/` (keep CLAUDE.md/README.md at root)? | **Yes** |
| D-8 | Milestone order: IMPLEMENTATION_PLAN (Requirements before Desktop/Voice)? | **Yes**, with optional M7 parallelism after M5 |
| D-9 | May I run `git init` (no commits) so tooling like pre-commit and checkpoints-on-self work? | **Yes**; commits remain approval-only |
| D-10 | May I install uv and run `rustup update stable` when M1/M7 start? | **Yes** |
| D-11 | Accept the proposed quantified NFR targets (§4.1) and new stories (§4.3)? | **Yes**, adjust as you see fit |
| D-12 | Run the S-1 voice feasibility spike during M0? | **Yes** |
