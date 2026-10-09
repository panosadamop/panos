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
