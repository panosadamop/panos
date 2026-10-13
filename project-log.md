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
- Committed M0–M2 as `e15ba02` (217 files) under the owner's Git identity.
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
- Committed M3+M4 as one commit, `4c8d088` (173 files). The two milestones share files (domain, persistence, container), so a clean split was not possible.
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
- Committed M5 as `b6cf9cb`.
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
- Committed M6 as `5e31f79`.
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


### 11. Commit of M7, then M8 — Integrations, research and documents

**Owner:** approved the M7 commit and M8 (M7 is committed as `2da16ad`), and decided on 2026-10-09:
- Gmail / Google Calendar first;
- Jira Cloud and Azure DevOps;
- Playwright Chromium with an isolated profile;
- Google Drive, OneDrive/SharePoint and selected websites as document sources.

These decisions are recorded in `PLAN_PROPOSAL.md` §M8. *The exact wording of that exchange is not preserved: the session that started M8 ended without a log entry, and work resumed on 2026-10-12 with "continue from where we stopped".*

**Claude (first session, reconstructed from the working tree):**
- connector foundation (accounts, migration 0007, keyring secrets, Google loopback+PKCE, Microsoft device code, Jira/ADO tokens, allowlisted HTTP client);
- Gmail (read, drafts only) and Google Calendar (approved writes) tools;
- Jira/ADO clients and tools;
- Office/PDF extraction and Git history in the index;
- DOCX/PDF/XLSX/PPTX exports with `panos project export`.

**Claude (2026-10-12):**
- Fixed three breakages left in the tree (an import order, a mypy error, and a capabilities test still asserting "no email tools").
- **Found and fixed:**
  - `build_default_registry` dropped the requirements service, so work-item changes were never linked as evidence;
  - a `create` change ignored the change-set project;
  - a comment's id was reported as the work-item key (giving a wrong `/browse/` link).
- Added work-item tests (approval binding, tampering, partial failure, JSON-patch).
- M8-00 surfaces: `/connectors` API and `panos connect …`.
- M8-04: knowledge collections (folders, websites with robots.txt and limits, Google Drive, OneDrive/SharePoint), FR-020 ranked search (`/knowledge/search`, `panos knowledge …`, `knowledge.search` tool, conversation retrieval). Graph downloads follow the redirect without the token. **Found:** `build_messages` re-sorts retrieval by scope, which would have undone the tier order, so the tier now sets the scope.
- M8-03: read-only Playwright engine, `browser.read`/`browser.search`, a `research` job with citations, and `panos research`.
  - **Found on real Chromium:** a page's own `form.submit()` (blocked correctly) replaced the page with an error page. An init script now neutralises submission.
  - **Live checks:** DuckDuckGo/Brave show a bot check and Bing returns irrelevant results (R-15, an owner decision). Research with a given URL works end to end with qwen2.5:3b. The first answer cited the page's own footnote `[5]`; footnotes are now stripped, and the answer cites `[1]`.
- M8-07: chat intents (unread mail, agenda with conflicts, research) in Greek/English; desktop Connected accounts, Knowledge sources and project export; regenerated OpenAPI/TS types.
- M8-08: ADR-026 and ADR-027 (Proposed), `docs/planning/connectors-setup.md`, README.

Report: [`docs/planning/reports/M8.md`](docs/planning/reports/M8.md). M8 not committed. Awaiting the owner's live checks, decisions (R-15, ADR-026/027) and a commit decision.

### 12. Commit of M8, then M9 — Multi-PC control plane and web dashboard

**Owner:** "commit M8 and approve M9". Answers to the M9 questions (2026-10-12):
- the hub runs **on one of my PCs**;
- **LAN only**;
- dashboard sign-in by **passkey (WebAuthn)**;
- test machines: **this PC plus a Mac or Linux PC**.

**Claude:**
- Committed M8 as `8960600`.
- Decomposed M9 into M9-00..09 in `PLAN_PROPOSAL.md`: ADR-028/029; the hub service on PostgreSQL; a private CA with one-time enrollment, mTLS and revocation; an outbound node link; remote dispatch under the target's policy; shared approvals verified by the target; allowlisted read-only aggregates with a LOCAL_ONLY metadata filter; passkey auth; the React dashboard; two-node end-to-end tests and the owner guide.
- Noted for M9-01: the Docker daemon is not running on this PC and there is no local PostgreSQL. Tests will use an embedded PostgreSQL (or CI's), and Docker Compose is for the owner's hub.
- Implemented M9:
  - **ADRs:** ADR-028 and ADR-029 (Proposed).
  - **Hub service** (`services/control-plane`): PostgreSQL schema and migrations, a hash-chained hub audit, CA and server certificate, Docker Compose.
  - **Identity:** one-time enrollment with CA pinning; an mTLS node link with revocation (immediate, plus a sweep) and renewal over the link.
  - **Protocol:** allowlisted messages.
  - **Node side:** a link client, a handler that uses the node's own runner, policy and approvals, and a mirror publisher with the LOCAL_ONLY privacy filter.
  - **Hub operations:** dispatch, control, shared approval decisions, live read-only queries.
  - **Dashboard auth:** passkeys, sessions and CSRF.
  - **React dashboard** (`apps/dashboard`, 8 views, el/en, axe) served under a strict CSP.
  - **Surfaces:** CLI `panos hub …` / `panos node …`, node API `/remote`, desktop Settings → Hub.
  - **Docs:** owner guide `docs/planning/multi-pc-setup.md`.
- **Found and fixed while testing:**
  - The project inventory was sent only at connect (a project registered later never reached the hub). It is now republished on change.
  - The CLI crashed on "→" with redirected cp1252 output.
  - The end-to-end "hang" was an unread stderr pipe in the test harness.
- **Verified:**
  - real TLS on loopback;
  - a real node app joined, ran a dispatched command and was approved from the hub;
  - passkeys with a software authenticator and in real Chromium;
  - real `panos` processes on both sides (join, dispatch, policy on the target, revoke).
- **Deviation:** `panos job start --node` from non-hub PCs is deferred; dispatch is available from the dashboard and with `panos hub dispatch`.

Report: [`docs/planning/reports/M9.md`](docs/planning/reports/M9.md). M9 not committed. Awaiting the owner's live check with a second PC, ADR decisions and a commit decision.

### 13. Commit of M9, then M10 — Mobile companion, estimation learning, analytics

**Owner:** "commit M9 and approve M10". Answers to the M10 questions (2026-10-12):
- mobile: **both, PWA first**;
- phone: **text only**;
- actual effort from **logged hours + tracker worklogs + Panos job metrics**;
- learning: **suggest, the owner approves**.

**Claude:**
- Committed M9 as `3699d6c`.
- Decomposed M10 into M10-00..09 in `PLAN_PROPOSAL.md`: ADR-030/031; effort store with approval of actuals; manual logging; tracker worklog import; job metrics as a separate machine series; calibration with suggestions the owner approves; model/cost analytics; a PWA companion with a hub-relayed conversation; native-app contract; end-to-end tests and docs.
- Implemented M10:
  - **ADRs:** ADR-030 and ADR-031 (Proposed).
  - **Effort store** (migration 0008) with approval of actuals; manual logging; Jira/ADO worklog import (pending, deduplicated, ADO deltas); job metrics as a machine series.
  - **Calibration:** median hours per point, suggestions the owner approves, estimates that state their calibration.
  - **Model/cost analytics** on the node, combined across PCs on the hub.
  - **Phone companion:** a PWA (manifest, shell-only service worker, phone layout, Talk view through a new hub → node `converse` message, approvals badge).
  - **Desktop:** Effort view and analytics in Settings.
  - **Docs:** owner guide `docs/planning/mobile-and-estimates.md`; native-app contract `docs/api/mobile-contract.md`.
- **Corrected during design:**
  - The suggested hours-per-point was changed from "base × factor" to the median of each story's actual hours ÷ points, so baselines made with different values cannot skew it.
  - A `completes` flag was added so that unfinished stories do not bias the calibration.
- **Found while testing:** Playwright's request client ignores the browser's certificate pin, so the manifest check moved into the page.

Report: [`docs/planning/reports/M10.md`](docs/planning/reports/M10.md). The owner asked for M10 to be committed (2026-10-12). Still awaiting the owner's phone check and the ADR-030/031 decisions.

**Owner (2026-10-13):** "push it".
- Committed M10 as `8a2ede6`.
- The first push to `github.com/panosadamop/panos` (an empty repo) was rejected by GitHub push protection. `docs/traceability/coverage.md` printed the fake tokens from the redaction tests as pytest ids.
- With the owner's approval, history was rewritten to replace those ids with neutral names in `coverage.md` only, so every commit hash changed:
  - M0–M2 `e15ba02`, M3+M4 `4c8d088`, M5 `b6cf9cb`, M6 `5e31f79`;
  - M7 `2da16ad`, M8 `8960600`, M9 `3699d6c`, M10 `8a2ede6`.
  - The hashes in the docs were updated to match.
- The tests now have neutral ids (`7981cea`), so the report can no longer print token-shaped strings. `main` is pushed and tracks `origin/main`; the local backup branch was deleted.

**Owner (2026-10-13):** "accept ADR-030 and ADR-031". Both are Accepted unchanged and now binding: the PWA companion, the hub-relayed conversation, no third-party push, and calibration only after owner approval.

**Owner (2026-10-13):** "accept ADR-026 to ADR-029 and push". All four are Accepted unchanged and now binding: connectors, browser research, the control plane (hub), and node identity/mTLS. All ADRs (001–031) are now Accepted.
