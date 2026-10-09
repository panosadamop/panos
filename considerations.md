# Considerations

Points raised by Claude Code during analysis and implementation: issues found in the specs, risks, design trade-offs, deviations and open follow-ups. Each point records its current status.

Full detail lives in [`docs/planning/PLAN_PROPOSAL.md`](docs/planning/PLAN_PROPOSAL.md), the [ADRs](docs/adr/README.md), [S-1 findings](docs/planning/S-1_voice_feasibility.md) and the [milestone reports](docs/planning/reports/).

Status legend: **Resolved** · **Mitigated** (risk reduced, still relevant) · **Open** · **Accepted** (consciously accepted trade-off)

---

## 1. Specification inconsistencies

| # | Point | Status |
|---|---|---|
| I-1 | IMPLEMENTATION_PLAN puts Requirements before Desktop/Voice; ROADMAP has the reverse. | Resolved: IMPLEMENTATION_PLAN order (D-8) |
| I-2 | "Basic local knowledge/RAG" is in the MVP, but knowledge only appeared in post-MVP M8. | Resolved: lexical index in M3, vector/hybrid in M4 |
| I-3 | Approvals (M2) need `WAITING_FOR_APPROVAL`, but the job state machine was in M5. | Resolved: core state machine moved to M2 |
| I-4 | Requirement states differ across FR-010, REQUIREMENTS_PIPELINE and DATA_MODEL; "stale" was undefined. | Resolved: unified lifecycle + `stale` flag (ADR-014) |
| I-5 | One `Approval` entity was used for both tool actions and requirements. | Resolved: `ActionApproval` vs `RequirementApproval` |
| I-6 | The pipeline example references nonexistent FR-032 and mismatched IDs. | Resolved: marked illustrative |
| I-7 | AC IDs were unnumbered. | Resolved: `AC-USxxx-NN` |
| I-8 | Specs were in the root, but the structure doc said `docs/specs/`. | Resolved (D-7) |
| I-9 | Browser research (FR-016) and Jira/ADO (BR-016) were in no milestone. | Resolved: added to M8 |
| I-10 | The hotkey and notifications were placed in the Python OS adapter, though Tauri is the natural owner. | Resolved: ADR-015 |

## 2. Gaps filled

- NFRs outside SEC/PRV had no IDs and no measurable targets. **Resolved:** IDs and targets added (D-11).
- 17 of 26 stories had no acceptance criteria. **Resolved:** 60 new ACs (drafts; to be refined per milestone).
- BR-003/004/020/022/027/028 had no stories. In particular, **local-only privacy had no story or AC**. **Resolved:** US-005/024/044/045.
- The data model and API lacked entities and endpoints (conversation, tool invocation, AI usage, checkpoints/rollback, etc.). **Open:** added as milestones need them.
- No hardware baseline existed. **Resolved:** measured in S-1 (CPU-only reference machine).

## 3. Key risks

| # | Risk | Status / mitigation |
|---|---|---|
| R-1 | **A controlled shell can become an arbitrary shell**, e.g. `git commit` or `docker run` typed into a shell bypasses approvals. | Mitigated by design (ADR-007): argv-only execution, parser-based classification, unknown → approval. Implementation and bypass tests are in M2. **Accepted residual risk:** allowlisted test/build commands execute repository code. |
| R-2 | **Local-only leakage through derived context** (summaries, embeddings, screenshots, errors). | Mitigated by design (ADR-008): labels, sticky taint, egress check in every cloud adapter. Implementation in M4. |
| R-3 | **Custom "Panos" wake word** may be inaccurate or trigger falsely; "Panos" is a common name. | Open: hotkey is the primary MVP trigger. Training task M7-07a. Consider "Hey Panos". |
| R-4 | Greek/mixed STT and TTS quality offline on CPU. | Mitigated: whisper `small` + Greek default + vocabulary prompt; Piper + language splitter. No Greek Windows voice is installed (install "Stefanos" optionally). |
| R-5 | **Local models are slow on this laptop** (7B ≈ 5–7 tok/s; a 4k-token context takes 2–3 min to read). | Accepted: local-only coding runs as background jobs. Follow-ups: iGPU via `OLLAMA_IGPU_ENABLE=1` + AMD driver update; MoE model; Greek-capable model. |
| R-6 | Prompt injection from repos, web, documents and email. | Mitigated by design (ADR-023). Injection corpus tests from M4. |
| R-7 | Wayland limits global hotkeys, window info and capture on Linux. | Accepted: documented degraded mode. |
| R-8 | macOS permissions and notarization. | Open (M7). |
| R-9 | The repo was in OneDrive (sync and locking problems). | Resolved: moved (D-1) |
| R-10 | Old Rust; no uv. | Resolved |
| R-11 | Licences: Piper GPL-3.0, openWakeWord models and some voices non-commercial, Qwen 3B research licence. | Accepted for personal use (D-6). Revisit if Panos is ever distributed. |
| R-12 | Gmail has no draft-only OAuth scope (`gmail.compose` can send). | Open (M8): enforce in code with no send tool plus an endpoint allowlist. |
| R-13 | The MVP scope is very large. | Mitigated: strict milestone gates. |
| R-14 | `sqlite-vec` is pre-1.0. | Mitigated: behind a port. |

## 4. Design decisions worth remembering (see ADRs)

- **In-house orchestrator, policy engine and coding loop.** Frameworks like LangGraph would compete with Panos's job, approval and audit semantics. External coding agents (Claude Code, Aider) carry their own shell tools, which would bypass policy.
- **Approvals are bound to the exact action:** RFC 8785 canonical hash, HMAC token, single use, expiry. Git commits bind the staged tree hash.
- **Checkpoints use a shadow Git repo.** The user's history, index and stash are never touched, and non-Git projects are supported.
- **New projects are LOCAL_ONLY by default.** Cloud models stay unused until a project is explicitly marked STANDARD.
- **P3/P4 actions can never be loosened by configuration.** Project overrides can only tighten them.
- The **loopback API** requires a token, a Host allowlist (DNS-rebinding defense) and an Origin allowlist. There are no shell endpoints. Docs and the live schema are disabled.

## 5. Deviations from the original spec or plan

1. Migrations ship inside `panos_core` (not root `/migrations`) so the node can self-upgrade, including in a frozen build. *(ADR-004 amended)*
2. The CI Python matrix is 3.12 + 3.14 (not 3.13).
3. `platform/posix/` holds shared macOS/Linux code. Separate packages will be added only where implementations differ.
4. The pnpm/TypeScript workspace is deferred to M7.
5. The secret store has a `memory` backend for tests only. Production uses the OS keyring and refuses plaintext storage.

6. **(M2)** New shell action classes extend PERMISSIONS.md: `shell.read` P0, `build.test` P1, `shell.write` P2, `shell.delete` P3, `shell.unknown` P3, `net.fetch` P2, `net.upload` P3, `git.mutate.local` P1, `git.destructive` P3. `net.upload` is P3, not P4, because the spec fixes only *browser* uploads at P4.
7. **(M2)** Per-project policy overrides are passed in `ToolContext` until the project registry persists them (M3).

## 5a. Security incidents

| Date | Event | Handling |
|---|---|---|
| 2026-10-08 | **Microsoft Defender flagged two test command lines** (threat 2147948670). They contained attack-shaped strings (a download-and-execute and an HTTP-POST upload, used only as parser input), and the PowerShell parser used `-EncodedCommand`. The processes were blocked; nothing malicious ran. | Parser switched to stdin (`-Command -`). Attack-shaped strings are kept off command lines; test corpora use benign equivalents. **The owner may want to inform IT security**, since alerts on a managed laptop may be visible to them. |

## 5b. Accepted residual risks (M2)

- Allowlisted build/test commands run repository code (ADR-007).
- Windows Job Object assignment race at process start; the psutil fallback covers it.
- `fs.delete`/`fs.move` act on symlink targets inside the project, not on the link itself.
- Conservative classification causes extra approval prompts for some legitimate compound commands until M3 discovers project commands.

## 6. Implementation lessons (Windows-specific)

- pywin32 types such as `PySID` exist only in the stubs. Annotations must be lazy.
- uvicorn re-raises the shutdown signal. On Windows, a re-raised `SIGBREAK` kills the process before `finally`, so cleanup must happen in the app lifespan.
- A venv's `python.exe` is a launcher, so the child PID ≠ the spawned PID.
- Set `PYTHONUTF8=1`; the cp1252 console breaks Greek output.
- faster-whisper 1.2.1 cannot decode files with PyAV 19. Decode with soundfile and pass arrays.
- Without ORM relationships, SQLAlchemy does not order inserts by foreign key. Repositories flush on `add`.
- **PowerShell variable names are case-insensitive**: `$c` overwrote `$C` in the parser program.
- Endpoint protection treats `powershell -EncodedCommand` and download-cradle-like strings on command lines as malicious. Avoid both.
- `Path.write_text` emits CRLF on Windows. Write test fixtures as bytes.
- `types-pywin32` annotates `CreateJobObject` as returning `None` (stub bug).
- Windows `CreateProcess` and `shutil.which` search the current directory first. Resolve executables explicitly.

## 7. Open follow-ups

- [ ] Add a Git remote so CI runs on macOS/Linux. Commits need explicit owner approval.
- [ ] Decide whether to commit M0–M2 (not yet approved).
- [ ] Optionally tell IT security about the 2026-10-08 Defender detections (test strings, not malware).
- [ ] Ollama iGPU test (`OLLAMA_IGPU_ENABLE=1`) after an AMD driver update.
- [ ] Evaluate a Greek-capable local LLM and an MoE model (M4).
- [ ] CT2 MKL A/B and whisper.cpp + Vulkan for STT latency (M7).
- [ ] Train and validate the "Panos" / "Hey Panos" wake word (M7-07a).
- [ ] Optionally install the Windows Greek speech voice ("Stefanos").
- [ ] Delete ~242 MB of leftover Piper voices in `%USERPROFILE%\.cache\huggingface\hub`.
- [ ] Starlette TestClient httpx deprecation: migrate when FastAPI does.
