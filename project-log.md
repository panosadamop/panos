# Project Log

Chronological record of the working conversation between the project owner and Claude Code. Newest entries are at the bottom. Detailed artifacts are linked rather than repeated.

---

## 2026-10-08 — Session 1

### 1. Kickoff: analysis and plan, no implementation

**Owner asked:**
- read `CLAUDE.md`, `README.md` and every referenced spec;
- analyze the specification;
- identify inconsistencies, missing decisions and risks;
- propose ADRs;
- produce a backlog from `IMPLEMENTATION_PLAN.md`, mapped to BR/FR/NFR/US/AC IDs;
- propose the monorepo structure and the libraries;
- identify platform-specific functionality;
- define the MVP boundary and milestone dependencies;
- present the plan for approval.

**Standing rules from the owner:**
- no production code before approval;
- after approval, work milestone by milestone, running tests and lint/type checks and reporting coverage, changed files and limitations at each milestone end;
- update docs, then wait for approval;
- **never commit without explicit approval**.

**Claude did:**
- Read all 24 spec documents (~44 KB) and checked the toolchain.
- Wrote [`docs/planning/PLAN_PROPOSAL.md`](docs/planning/PLAN_PROPOSAL.md):
  - 10 inconsistencies, 8 gaps, 14 risks;
  - 25 proposed ADRs;
  - backlog M0–M10, about 70 traced items;
  - reverse coverage matrix;
  - monorepo layout, libraries and platform matrix;
  - MVP = M0–M7;
  - 12 decisions for the owner (D-1…D-12).

### 2. Decisions

**Owner decided:**

| # | Decision |
|---|---|
| D-1 | Move the project to `C:\Users\padamop\PycharmProjects` |
| D-2 | Windows is the first supported OS |
| D-3 | New projects default to LOCAL_ONLY |
| D-4 | Ollama is OK as local runtime; GPU unknown |
| D-5 | Anthropic + OpenAI first |
| D-6 | Personal/internal use |
| D-7 | Specs into `docs/specs/` |
| D-8 | IMPLEMENTATION_PLAN order |
| D-9 | `git init` allowed |
| D-10 | Install uv and update Rust |
| D-11 | Accept NFR targets and the new stories |
| D-12 | Run the voice spike |

**Claude did (M0 — pre-work):**
- **Hardware detected:** Ryzen 7 PRO 7735U, Radeon 680M iGPU (no discrete GPU), 62 GB RAM.
- **Moved** the repo to `C:\Users\padamop\PycharmProjects\panos` (copy verified by hash; old OneDrive folder emptied).
- **Tooling:** `git init`; installed uv 0.12.23; Rust 1.83 → 1.99.
- **Specs** moved to `docs/specs/`. Amended:
  - NFR IDs and targets;
  - US-005/024/044/045;
  - ACs numbered `AC-USxxx-NN`, plus 60 new ACs;
  - dated amendment notes in 8 specs.
- Wrote ADR-001…025 in [`docs/adr/`](docs/adr/README.md).
- Built the traceability registry: [`scripts/trace_registry.py`](scripts/trace_registry.py) → `docs/traceability/requirements.yaml` (217 IDs).
- Saved project memory for future sessions.

### 3. S-1 voice and local-LLM spike

- A background agent ran the throwaway spike. Results are in [`docs/planning/S-1_voice_feasibility.md`](docs/planning/S-1_voice_feasibility.md).
- The owner asked how long the spike would take. Claude checked live progress and shared interim findings. The owner chose to let it finish.
- **Outcome:**
  - Everything runs on CPU only.
  - **LLM speeds:** 3B ≈ 11–13 tok/s, 7B coder ≈ 5–7 tok/s, 14B ≈ 2.8 tok/s.
  - **STT:** whisper `small` ≈ 2.7 s per command.
  - **Wake word:** openWakeWord is cheap; a custom "Panos" model needs WSL2 training.
  - **TTS:** Piper with Greek and English voices plus a language splitter.
  - Ollama is installed with 4 models.
- ADR-009 and ADR-016 were finalized from these measurements. Backlog item M7-07a (wake-word training) was added.
- M0 report: [`docs/planning/reports/M0.md`](docs/planning/reports/M0.md).

### 4. M1 — Skeleton

**Owner:** "approve M1".

**Claude did:**
- uv workspace (`panos-core`, `panos-node-api`, `panos-cli`).
- Layered settings.
- Domain model: Project, Job (11 states), JobStep, ActionApproval, AuditEvent, Node, Checkpoint.
- SQLite + SQLAlchemy 2.1 + in-package Alembic migrations.
- structlog with redaction.
- FastAPI `/health` and `/capabilities`, protected by token, Host and Origin checks.
- CLI `serve`/`status`/`config`, with single instance and an owner-only runtime file.
- pytest requirement-tagging plugin and coverage report.
- CI workflow.

**Results:**
- 133 passed, 1 skipped, coverage 89.4 %.
- ruff, mypy strict and import-linter clean, on Python 3.12 and 3.14.

**Bugs found and fixed:**
- pywin32 stub-only type crashed startup;
- uvicorn's re-raised SIGBREAK on Windows left a stale runtime file;
- foreign-key insert ordering.

Report: [`docs/planning/reports/M1.md`](docs/planning/reports/M1.md).

### 5. M2 — Policy and tools (in progress)

**Owner:** "approved". Interpreted as approval to start M2 only. **No commit was made**, because commit approval must be explicit.

**Claude so far:**
- Added dependencies: pyyaml, rfc8785, keyring, bashlex, psutil.
- Secret store (keyring; memory backend for tests only).
- Redaction v2: more provider formats, plus entropy detection tuned to avoid false positives on hashes, IDs and code identifiers.

**Owner asked (mid-M2):** record the conversation in `project-log.md` and Claude's points in `considerations.md`. Done (this file and [`considerations.md`](considerations.md)). Both are now kept up to date as a standing rule (also added to `CLAUDE.md`).

**Claude completed M2:**
- **Policy engine:** default deny; the YAML default policy mirrors PERMISSIONS.md.
- **Tools:** contract with runtime-issued execution permits; registry that structurally refuses `email.send`.
- **Runtime pipeline:** the single execution path.
- **Approvals:** RFC 8785 hash, HMAC token, single use, expiry, invalidation when the action changes.
- **Job state machine.**
- **Filesystem tools:** with a Windows-hardened path gate.
- **Controlled shell:** classifier using the bashlex, PowerShell-AST and cmd parsers; process-tree kill; env scrubbing; BatBadBut guard.
- **Audit:** hash-chained (migration 0002).
- **Redaction v2.**
- **Keyring secrets.**
- **Security suite.**

**Incident:**
- Microsoft Defender flagged two of Claude's test command lines (attack-shaped parser inputs, plus `-EncodedCommand`) as threat 2147948670 and blocked them. Nothing malicious ran.
- The parser was changed to use stdin, and test inputs were made benign. The owner was advised to inform IT if needed.

**Results:**
- 557 passed, 1 skipped; coverage 89.1 %.
- Critical modules at 95–100 % branch coverage.
- ruff, mypy and import-linter clean.

Report: [`docs/planning/reports/M2.md`](docs/planning/reports/M2.md). Awaiting approval for M3. No commits made.

## 2026-10-09 — Session 1 (continued)

### 6. Commit and M3 — Projects and repository intelligence

**Owner:** "yes", in answer to "approve M3?" and "commit M0–M2?". Claude treated it as approval of both.

**Claude:**
- Committed M0–M2 as `f53b847` (217 files) under the owner's Git identity.
- Inventoried the machine's installed apps (read-only), then implemented M3:
  - project registry (refuses roots, home and system dirs), with persisted per-project policy overrides and an audited privacy change;
  - stack/build/test detection for 10 stacks (fixture repos);
  - repository snapshot (outline, conventions, docs, Git state, confidence and clarification questions);
  - typed Git tools with option-injection protection;
  - desktop adapter contract plus the Windows production adapter (13 apps discovered on this machine);
  - app catalog/allowlist and preferred-IDE resolution;
  - app, window and knowledge tools;
  - FTS5 knowledge index with Greek accent-insensitive search;
  - routers package, and the CLI `project` and `apps` commands.

**Live demo on the real machine:**
- register, analyze, index, search and open (dry run → PyCharm) on the Panos repo itself;
- the demo found two quality bugs (fixture-driven false infrastructure detection; unreadable normalized snippets), both fixed.

**Other bugs fixed:**
- app-discovery race (UNIQUE violation);
- Greek search not matching (FTS5 folds only Latin accents);
- Edge's version-specific path.

**Results:**
- 656 passed, 1 skipped; coverage 85.6 %.
- ruff, mypy and import-linter clean.

Report: [`docs/planning/reports/M3.md`](docs/planning/reports/M3.md). M3 not committed. Awaiting approval for M4.

### 7. M4 — AI layer, privacy routing, conversation

**Owner:** "approved" (M4). Claude treated it as approval of M4 only. M3 stays uncommitted until the owner explicitly approves a commit.

**Claude:**
- Built the provider-neutral AI layer:
  - ports and types with privacy labels;
  - adapters for Ollama, OpenAI-compatible, Anthropic (official SDK; current-model rules: no sampling params, adaptive thinking + `effort`, refusal fallback) and mock;
  - model registry with S-1 speeds;
  - explainable router with hard privacy filter, budgets and offline fallback;
  - single gateway (redaction, metering, audit);
  - context builder with forgery-proof untrusted envelopes.
- Added the conversation service:
  - sticky taint; unscoped conversations are LOCAL_ONLY;
  - rule-based Greek/English intents that run through the tool runtime with no model;
  - retrieval with sources;
  - streaming over NDJSON and `/ws/events`.
- Added vectors: sqlite-vec + nomic-embed-text, hybrid RRF search.
- CLI: `panos ask`, `panos ai …`.
- **Exit-criterion evidence:** router property test; adapter-guard tests (I/O never reached); full-stack spy suite with a planted local-only marker and an injection attempt.

**Bugs found and fixed:**
- Cloud providers list dated model IDs, so a model-name filter would have hidden Claude.
- Search lost lexical results when the embedding model was down.
- CLI sent `semantic=` (empty) → 422.
- Starlette TestClient is built on `httpx2`. CLI tests bridge through a real `httpx` transport.
- A pre-existing audit test raced with startup app discovery.

**Live:**
- `panos ask` through a real node to local qwen: about 35 s, `LOCAL_ONLY`.
- Retrieval smoke with real embeddings: hybrid 3/5 top-1 vs lexical 0/5; Greek→English cross-lingual weak (follow-up).

**Results:** 752 passed, 1 skipped; coverage 86.9 %; ruff, mypy (219 files) and import-linter clean; OpenAPI and registry up to date.

Report: [`docs/planning/reports/M4.md`](docs/planning/reports/M4.md). M3 and M4 not committed. Awaiting approval for M5 and a commit decision.

### 8. Commit of M3–M4, then M5 — Orchestration and coding

**Owner:** "approve both", in answer to "approve M5?" and "commit M3 and M4?".

**Claude:**
- Committed M3+M4 as one commit, `58de356` (173 files). The two milestones share files (domain, persistence, container), so a clean split was not possible.
- Implemented M5:
  - asyncio job runner (recovery to PAUSED, pause at safe points, resume from persisted state, cancel with process-tree kill, approval-driven resume, limits);
  - shadow-Git checkpoints with exact-byte rollback;
  - agent contract plus Planner, Developer, Reviewer and QA roles;
  - coding workflow with bounded repair loop;
  - `git.commit` bound to the exact staged tree (temporary index);
  - `docker.inspect`;
  - notifications;
  - `/jobs` API and `panos job` CLI;
  - work requests in chat start jobs.

**Evidence:**
- Fixture-repo jobs with scripted agents: plan → checkpoint → edits → `python manage.py test` → review → commit approval.
- Tests for approve, reject and change-after-approval (invalidated).
- Repair success, and repair exhaustion (BLOCKED, never COMPLETED).
- Rollback that leaves the user's HEAD, index and stash unchanged.
- Hard-kill e2e on the real node: job recovered as PAUSED/interrupted, and the tool's child process died with the node.

**Live run with local Ollama models:**
- The full pipeline ran in about 6 minutes.
- qwen2.5-coder:7b produced a wrong edit, and its repair reply used raw newlines inside JSON strings. The job ended FAILED.
- Fixed: the JSON parser now accepts raw newlines in strings, and unusable model output now blocks the job (resumable) instead of failing it.
- Two more live runs: the job ended BLOCKED with clear reasons (missing import, then an edit anchor not in the file).
- Further fixes: model-output errors now include a reply excerpt, and a fix that cannot be applied now counts as a failed repair attempt.
- Conclusion: the 7B local model cannot finish even a trivial feature unattended; the pipeline itself behaves correctly.

**Results:** 792 passed, 1 skipped; coverage 87.6 %; ruff, mypy (245 files) and import-linter clean. Two test-only races fixed along the way (wait helper on the wrong runner; process liveness check).

Report: [`docs/planning/reports/M5.md`](docs/planning/reports/M5.md). M5 not committed. Awaiting approval for M6 and a commit decision.

### 9. Commit of M5, then M6 — Requirements pipeline

**Owner:** "approve both", in answer to "approve M6?" and "commit M5?".

**Claude:**
- Committed M5 as `839319d`.
- Implemented M6:
  - versioned requirements with stable per-project keys (16 types);
  - cascade approval with exclusions and per-item records;
  - trace links and evidence;
  - traceability query with gaps;
  - change control: transitive stale marking, an impact-analysis job that waits for the user, and coding jobs blocked on unapproved/stale requirements;
  - persisted, resumable interview (Business Analyst questions, answers as provenance, skips as assumptions);
  - spec generation into drafts;
  - stack packs (7 frameworks + 4 add-ons) with explicit-stack detection and a mandatory approved stack ADR;
  - offline scaffolding;
  - Markdown/OpenAPI documents (golden-tested);
  - estimation and sprint plan;
  - versioned Claude skills from approved facts;
  - API and CLI (`panos new`, `interview`, `req`, `project estimate|docs|skills|scaffold`).

**Found and fixed while testing:**
- Golden review: missing blank lines before headings; pack conventions showing raw `{{ package }}`.
- A failed model call left a half-created project behind. The project is now created only after the brief has been analysed.

**Live run with local models:**
- qwen2.5-coder:7b asked 10 good questions in about 2 minutes. They are now capped at 8, as instructed.
- The full specification exceeded the 300 s HTTP timeout and was reported as "ollama unreachable". Now: local runtimes default to a 30-minute timeout, and timeouts are a clear, non-retried error.
- Rerun: 8 questions in 95 s, then a valid 27-item specification in 205 s that reflects the answers and the requested stack. The content is thin, so drafts need review before approval.

**Results:** 845 passed, 1 skipped; coverage 88.2 %; ruff, mypy (267 files) and import-linter clean; OpenAPI and registry up to date.

Report: [`docs/planning/reports/M6.md`](docs/planning/reports/M6.md). M6 not committed. Awaiting approval for M7 and a commit decision.

### 10. Commit of M6, then M7 — Desktop and voice

**Owner:** "approve both", in answer to "approve M7?" and "commit M6?".

**Claude:**
- Committed M6 as `3ba0038`.
- Implemented M7:
  - voice worker (separate supervised process) with push-to-talk, an ONNX wake word (dev model, opt-in), Silero VAD, faster-whisper `small`, Piper with a Greek/English splitter, barge-in, and routing ("stop talking" vs. job control);
  - `panos voice …` CLI, `/voice/*` API, spoken notifications for what needs the user;
  - active-window capture and local-first vision routing (`panos screen`, `/screen/analyze`, chat/voice intent);
  - replayable event bus (seq, epoch, resync);
  - React desktop UI (8 views, el/en, axe-checked) with generated REST and event types;
  - Tauri 2 shell (attach or start the node, tray, single instance, autostart, push-to-talk hotkey, notifications, no fs/shell capabilities);
  - PyInstaller sidecar and the NSIS installer;
  - wake-word recording and validation tooling, plus a training guide.

**Found and fixed while testing:**
- `openwakeword` needs `tflite-runtime` on Linux (no Python 3.12+ wheels). Reimplemented its pipeline on onnxruntime with identical scores.
- Live microphone: the utterance ended before the speaker started, and Whisper looped for 31 s on Greek. Added a grace period after the wake word, minimum speech before endpointing, a token cap and `no_repeat_ngram_size`.
- Screen requests with cloud consent did not prefer the cloud model, and the Greek question mark (`;`) was not recognised in the screen intent.
- TypeScript 7 broke openapi-typescript (pinned ~5.9); duplicate OpenAPI schema names.
- Frozen build: Alembic `env.py` missing, and `aiosqlite` not detected. Both were found only by a real `serve` smoke test.
- Report review: the UI had no autostart toggle (added), and the "speak notifications" checkbox ignored the node's setting (fixed, with a test).

**Live checks:**
- Real microphone and worker: ready in 16 s, push-to-talk, Greek and English TTS aloud.
- Frozen node: healthy in 7.5 s; the frozen voice worker reached idle with real models.
- Active-window capture works on Windows.
- Synthetic code-switched audio transcribes poorly, so mixed-language STT was **not** claimed; it needs real speech.

**Results:** 880 passed, 1 skipped (real voice models enabled); coverage 86.9 %; ruff, mypy (296 files) and import-linter clean; OpenAPI, registry and event types up to date; desktop 17/17 tests with axe; clippy and Rust tests clean; NSIS installer built (not run).

**Owner checks still open:**
- install the app and try the window, tray, hotkey and notifications;
- record and train the "Panos" wake word;
- real mixed-language speech;
- optionally pull a local vision model.

Report: [`docs/planning/reports/M7.md`](docs/planning/reports/M7.md). M7 not committed. Awaiting approval for M8 and a commit decision.

