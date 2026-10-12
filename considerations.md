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
| R-12 | Gmail has no draft-only OAuth scope (`gmail.compose` can send). | Mitigated (M8, ADR-026): the send endpoints are not on the allowlist, a draft id can never be `send`, and the registry refuses an `email.send` tool. Residual: the token itself could send if extracted from the keyring. |
| R-15 | **Web search engines block or degrade automated browsers.** Live check 2026-10-12: DuckDuckGo and Brave show a bot check; Bing returns irrelevant results. | Open (owner decision): research with given URLs works; search reports "blocked" clearly. Options: a search-API provider (e.g. Brave Search API with the owner's key, behind an adapter) or keep URL-driven research. No bot-detection evasion will be built. |
| R-16 | Connected accounts hold long-lived tokens that can change external systems. | Mitigated (ADR-026): keyring only, allowlists, P3 approval bound to the exact change set, LOCAL_ONLY by default, audit for connect/privacy/disconnect. |
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
4. The pnpm/TypeScript workspace is deferred to M7. *(Done in M7: `apps/desktop`.)*
5. The secret store has a `memory` backend for tests only. Production uses the OS keyring and refuses plaintext storage.

6. **(M2)** New shell action classes extend PERMISSIONS.md: `shell.read` P0, `build.test` P1, `shell.write` P2, `shell.delete` P3, `shell.unknown` P3, `net.fetch` P2, `net.upload` P3, `git.mutate.local` P1, `git.destructive` P3. `net.upload` is P3, not P4, because the spec fixes only *browser* uploads at P4.
7. **(M2)** Per-project policy overrides are passed in `ToolContext` until the project registry persists them (M3).

8. **(M3)** No tree-sitter yet: symbol outlines use Python `ast` plus regexes (names only). A full parser is deferred to M5.
9. **(M3)** The knowledge index (`knowledge.db`) uses `PRAGMA user_version` rather than Alembic. It is a rebuildable cache.
10. **(M3)** The application catalog is the "approved application" allowlist. There is no user-editable list yet.
11. **(M4)** Embeddings use `nomic-embed-text` via Ollama instead of fastembed (no second ONNX runtime; the model was already pulled in S-1). *(ADR-017 note)*
12. **(M4)** Tokens stream over the HTTP response (NDJSON) *and* as `conversation.delta` events on `/ws/events`. The CLI uses NDJSON; the desktop app can use either.
13. **(M4)** The WebSocket lives at `/api/v1/ws/events` (versioned like the rest of the API), not at the bare `/ws/events` in API.md.
14. **(M4)** Intent classification is rule-based (English + Greek), not a local LLM call. Simple commands answer in milliseconds; everything else goes to the router. An LLM classifier can be added behind the same `Intent` type.
15. **(M4)** Budget escalation uses the *cheaper fallback* path only (over-budget cloud candidates are filtered out). Approval-gated escalation arrives with jobs in M5.
16. **(M4)** No vision port yet (the `VISION` task type exists; image content does not). Screenshots/vision come with M7.
17. **(M5)** Only Planner, Developer, Reviewer and QA agents exist. The other AGENTS.md roles come with their workflows (requirements M6, DevOps/planning later).
18. **(M5)** Review findings are reported, not auto-fixed. The trace check only reports which requirement IDs travelled through the job and appear in the plan.
19. **(M5)** Docker has no dedicated execution tool. Execution goes through `shell.run` + classifier (`docker.exec` P3), which already gives "inspect without approval, execute only with approval".
20. **(M5)** Notifications go to the event bus (`/ws/events`) and the log only; desktop toasts and voice arrive in M7.
21. **(M6)** Requirement endpoints are project-scoped (`/projects/{ref}/requirements/{key}…`) instead of API.md's `/requirements/{id}`, because keys are unique per project, not globally.
22. **(M6)** "8 stack families" are delivered as 7 application packs (FastAPI, Django, Spring Boot, Vaadin, React/TypeScript, NestJS, Laravel) plus 4 add-ons (PostgreSQL, MongoDB, Docker, Kubernetes), which covers every technology BR-008 names. The cloud providers in BR-008 (GCP/Azure/AWS) have no scaffold templates yet.
23. **(M6)** Only Markdown and OpenAPI artifacts are generated. DOCX/XLSX/PPTX/PDF export (FR-029, BR-028) is still open.
24. **(M6)** Official project generators (Spring Initializr, `create-vite`, …) are not used; scaffolding is template-based and offline.
25. **(M7)** The `openwakeword` package is not used. Its Linux install needs `tflite-runtime`, which has no wheels for Python ≥ 3.12 (our CI matrix). `OnnxWakeWord` runs the same models on onnxruntime with identical scores. *(ADR-016 note)*
26. **(M7)** UI tests are Vitest + Testing Library + axe on all 8 views against a fake API. There is **no Playwright suite** yet (M7-03 listed one); the event-stream client has its own reconnect and resync tests.
27. **(M7)** TypeScript is pinned to ~5.9: openapi-typescript does not work with TypeScript 7 yet.
28. **(M7)** Screen capture on macOS/Linux grabs the whole primary screen (no window bounds yet), with no macOS permission flow and no Wayland support. Windows is the production path, as ADR-022 allows.
29. **(M7)** Model downloads (`panos voice setup`) use pinned release tags over HTTPS, but **without checksum verification** (ADR-021 asks for it).
30. **(M7)** Only the Windows NSIS installer is built; macOS/Linux bundles are not (M7-09 says best-effort).

## 5a. Design points added in M3

- **Project registration refuses dangerously broad scopes** (filesystem roots, home, system dirs, Panos data dirs), because a project defines the tools' working scope.
- **Discovered format/lint/type-check/test/build commands become P1** for that project; everything else keeps its classification.
- **`open_path` never opens executables.** App launch arguments are limited to the project path or an http(s) URL in a browser.
- **Git tools block option injection.** Revisions may not start with `-`; paths always follow `--`.
- **Search normalizes text** (Greek tonos/case) for matching but shows the original text.
- Fixture and sample directories don't shape the detected stack.

## 5a-bis. Design points added in M4

- **Unscoped conversations are LOCAL_ONLY by default** (`ai.unscoped_privacy`). Talking to Panos without a project never reaches the cloud unless the owner changes that setting.
- **Sticky taint applies per conversation, not per project.** A new conversation in a project that is now STANDARD may use the cloud. A tainted conversation's history is never sent.
- **Cloud adapters refuse unredacted requests.** Redaction can therefore not be skipped by calling an adapter directly.
- **Remote Ollama or OpenAI-compatible servers are cloud** unless they are on loopback or on an IP explicitly marked `trusted_lan`. DNS names other than localhost are always cloud.
- **API keys never come from the environment or argv.** Only the keyring, via `panos ai set-key` (hidden prompt / `--stdin`).
- **Chat routing for STANDARD content prefers Claude Sonnet 5.5** (AI_ROUTING "cloud_efficient"), then local models. The router reports expected cost; per-request ($0.50) and monthly ($20) caps are on by default.
- **The Anthropic refusal fallback is on by default** (server-side, Sonnet/Opus 5.5). If Claude declines a request, the API can retry it on a fallback model. Turn it off with `ai.anthropic_refusal_fallback = false`.

## 5a-ter. Design points added in M5

- **Commits are bound to the exact tree.** `git.commit` computes the tree in a temporary index (the user's index is never touched) and the approval hashes `{message, paths, HEAD, tree}`. Any change after approval invalidates it. Unrelated changes the user has staged stay staged and uncommitted.
- **A coding job never commits on its own.** It ends WAITING_FOR_APPROVAL with the diffstat in the approval summary. Rejecting leaves the work uncommitted and the job COMPLETED, with an "uncommitted" note.
- **Checkpoints use a shadow repository** outside the project, with exact-byte restore and an undo point taken before every rollback.
- **Every failure path ends visibly:** limits, exhausted repairs, invalid edits and rejected approvals end BLOCKED with a report. Nothing unsuccessful is ever marked COMPLETED.
- **Work requests in conversation start jobs** ("implement …", "φτιάξε …" with a project); without a project they are answered as chat.
- **Docker execution stays in `shell.run`** (`docker.exec`, P3). `docker.inspect` only parses files and, if the Docker CLI exists, runs read-only `ps` / `compose config`.

## 5a-quater. Design points added in M6

- **A new project is created only after the model has analysed the brief.** If no suitable model is available, nothing is left behind and the same name can be reused.
- **Briefs and specifications of new projects are LOCAL_ONLY** like any new project, so the interview runs on local models unless the owner relaxes the project.
- **A stack named in the brief is honoured without a model call** (US-021). Otherwise the Architect proposes one from the declared packs only. Either way it is an ADR that **must be approved before scaffolding**.
- **Skipped interview questions become explicit assumptions** with provenance, so silence is never treated as an answer.
- **Scaffolding is offline and never overwrites files.** It creates a local Git repository but **no commit**; commits keep their approval flow.
- **Skills contain approved, non-stale facts only.** They are versioned by a hash of exactly those facts, so unrelated changes do not bump them.
- **Change control is transitive over trace links,** not just the parent tree. Re-approving a parent clears its descendants; linked items (APIs, data) need their own decision or an explicit, reasoned clear.

## 5a-quinquies. Design points added in M7

- **Push-to-talk is the primary voice trigger.** The wake word is off by default, and `hey_jarvis` is only a development stand-in until a "Panos" model meets the validation targets.
- **Voice is a separate, supervised process.** A crash cannot take the node down. If the node dies, the worker exits on stdin EOF. Missing models are reported, never downloaded implicitly.
- **"Stop talking" is never a job command.** It only stops speech; "stop" / "pause" / "resume" act on jobs. Voice notifications speak only what needs the user.
- **Screenshots stay in memory and local by default.** Cloud vision requires per-request consent **and** a STANDARD project; consent alone does not change a LOCAL_ONLY label.
- **The desktop shell has no file-system or shell capability.** The WebView gets the node's base URL and token only through the `connection` command and keeps them in memory.
- **Events are replayable.** Each event has a sequence number, and the node keeps the last 2000 for reconnecting clients; anything older triggers a resync over REST.

## 5a-sexies. Design points added in M8

- **Connectors use the owner's own OAuth apps.** Tokens live in the keyring; the database knows only labels and granted features. Every connector request passes an allowlist (method + host + path) before it is sent (ADR-026).
- **Mail is read + draft only.** Calendar and work-item writes are prepared into exact requests and need P3 approval of that exact payload. A tampered set invalidates the approval.
- **Work-item changes become requirement evidence** (`workitem:<system>:<key>`) when a requirement key is given (AC-US042-02). Changes to an existing item report that item's key, not the comment id.
- **Knowledge collections** (folders, websites, Drive, OneDrive/SharePoint) share the index with projects and are LOCAL_ONLY by default. Retrieval ranks the current project first, then company knowledge, then websites (FR-020). The tier also sets the prompt-context scope, so `build_messages` keeps that order. Conversations now retrieve from collections even without a project; a LOCAL_ONLY collection hit therefore keeps such a turn local.
- **Graph downloads** follow the 302 to the pre-authenticated URL **without** the bearer token.
- **Browser research is read-only by construction** (ADR-027): there are no click/fill/upload operations, non-GET requests are aborted, in-page form submission is neutralised, and private networks are blocked. The registry refuses `browser.submit`/`browser.upload` tools.
- **Web tools are refused in LOCAL_ONLY projects**, since a query or URL could carry protected context. "Research …" in such a chat runs outside the project and says so.
- **Research is a job** (pause/resume/cancel at safe points; read pages are kept in the state). Page footnote markers like `[5]` are stripped before answering: in the live run they made qwen2.5:3b cite the page's footnote instead of the source number.
- **New chat intents** (Greek/English): unread mail, today's/tomorrow's agenda with conflicts, and "research …". Mail and calendar replies need no model and keep the content's privacy label.

## 5b. Security incidents

| Date | Event | Handling |
|---|---|---|
| 2026-10-08 | **Microsoft Defender flagged two test command lines** (threat 2147948670). They contained attack-shaped strings (a download-and-execute and an HTTP-POST upload, used only as parser input), and the PowerShell parser used `-EncodedCommand`. The processes were blocked; nothing malicious ran. | Parser switched to stdin (`-Command -`). Attack-shaped strings are kept off command lines; test corpora use benign equivalents. **The owner may want to inform IT security**, since alerts on a managed laptop may be visible to them. |

## 5c. Accepted residual risks (M2)

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
- A frozen Playwright looks for browsers inside the bundle (`_internal/playwright/driver/.local-browsers`). Set `PLAYWRIGHT_BROWSERS_PATH` to the per-user cache. Found only by the frozen smoke test (M8).
- PyInstaller does not detect modules imported by name at runtime: the SQLAlchemy dialect driver (`aiosqlite`) needs `--hidden-import`, and Alembic's `env.py`/migrations need `--add-data` (they are loaded as source files). **Smoke-test the frozen build, not just `version`.**
- openWakeWord's Linux dependency `tflite-runtime` has no Python 3.12+ wheels. The ONNX models give identical results without it.
- Silero VAD v5 expects a 64-sample context before every 512-sample chunk; without it the scores are wrong.
- faster-whisper can loop on short or noisy Greek audio ("Παραδείγματος…" for 31 s). Cap `max_new_tokens` by duration and set `no_repeat_ngram_size`.
- Synthetic (TTS-generated) code-switched audio is a poor STT test: Whisper transcribes it badly even when real speech works. Use real recordings as evidence.
- TypeScript 7 breaks openapi-typescript; pin TypeScript ~5.9.

## 7. Open follow-ups

- [ ] Add a Git remote so CI runs on macOS/Linux. Commits need explicit owner approval.
- [x] Commit M0–M2 — done 2026-10-09 (`f53b847`).
- [x] Commit M3 — done 2026-10-09 (`58de356`, together with M4).
- [ ] Coverage under-reports async API handlers run in TestClient's portal thread. Investigate (e.g. an httpx `ASGITransport` client) if API-layer coverage numbers matter.
- [ ] Honour nested `.gitignore` files in the walker/indexer.
- [x] Secret redaction before cloud prompts (M4, `code_safe` redaction in the gateway). The *index* itself still stores raw text locally; that is by design for a local cache.
- [x] Commit M4 — done 2026-10-09 (`58de356`).
- [ ] Retrieval uses FTS snippets (~matched lines), not whole chunks. Pass full chunk text to the model when context budgets allow.
- [ ] An LLM-based intent classifier on the local light tier, for paraphrased commands the rules miss.
- [ ] Approval-gated budget escalation: still open. M5 limits block the job; the user resumes it, optionally with a larger budget via a new job.
- [x] DOCX/XLSX/PPTX/PDF export of the requirement documents (FR-029, BR-028) — M8-05.
- [ ] Coding jobs should record `implements` evidence links (commit, files) for the requirement IDs they touch.
- [x] Live interview and specification run with local models (M6 report): works in about 5 min; content thin.
- [ ] Follow-up interview rounds (questions based on the first answers) and a review/critique pass on the generated specification.
- [ ] Checkpoint retention pruning (`jobs.checkpoint_retention` unused), a large-file limit, and nested Git repos in checkpoints.
- [ ] Approval tokens are memory-only. An approval granted just before a node restart must be re-requested (resume re-issues it). The 30-minute approval TTL may be short for unattended jobs.
- [ ] Planning a commit writes blob objects into the user's object store (no refs or index change; `git gc` prunes unused ones). Git hooks run on the real commit; if a hook rewrites content, `tree_matches_approval: false` is reported.
- [x] Live coding runs with local models (M5 report): the pipeline works, but qwen2.5-coder:7b did not complete the task in 3 runs.
- [ ] A more forgiving edit format for small local models (search/replace blocks instead of JSON), and evaluating qwen2.5-coder:14b / an MoE model for local-only coding.
- [ ] Greek-capable local model evaluation is still open (qwen2.5 Greek prose is weak).
- [ ] Multilingual embeddings: in the live smoke, `nomic-embed-text` got 3/5 top-1 overall but missed Greek-query → English-content cases. Evaluate `bge-m3` or `multilingual-e5` via Ollama (the registry allows a model swap; vectors are keyed per model).
- [ ] Optionally tell IT security about the 2026-10-08 Defender detections (test strings, not malware).
- [ ] Ollama iGPU test (`OLLAMA_IGPU_ENABLE=1`) after an AMD driver update.
- [ ] Evaluate a Greek-capable local LLM and an MoE model (carried over from M4).
- [ ] CT2 MKL A/B and whisper.cpp + Vulkan for STT latency (M7).
- [ ] Train and validate the "Panos" / "Hey Panos" wake word (M7-07a).
- [ ] Optionally install the Windows Greek speech voice ("Stefanos").
- [ ] Delete ~242 MB of leftover Piper voices in `%USERPROFILE%\.cache\huggingface\hub`.
- [ ] Starlette TestClient httpx deprecation: migrate when FastAPI does.
- [ ] Real-speech evaluation of mixed Greek/English commands (AC-US003-01/02). Synthetic audio was not accepted as evidence.
- [ ] Checksum verification for voice model downloads (ADR-021).
- [ ] Pull and evaluate a local vision model (`gemma3:4b` / `qwen2.5vl:7b`) for screen analysis on the CPU-only machine.
- [ ] The `screen.captured` audit record stores the window title (first 200 chars). It can reveal private content (e.g. an email subject); consider an option to hash or omit it.
- [ ] CI: voice wheels (onnxruntime, ctranslate2, Piper) for Python 3.14 on all three OSes are a risk; the voice extra may need to be optional in the 3.14 job.
- [ ] Run the NSIS installer and do the manual desktop check (window, tray, hotkey, notifications, autostart) on Windows.
- [ ] Playwright end-to-end UI tests against the mock API (M7-03 asked for them; Vitest + axe cover the views today).
- [ ] macOS/Linux: window bounds for capture, the macOS screen-recording permission flow, Wayland via the portal, and installer bundles.
- [ ] Code signing of the installer (optional for personal use).
- [ ] **Owner decision (M8):** web search provider for research (R-15): a search API behind an adapter vs URL-driven research only.
- [ ] Accept or amend ADR-026 and ADR-027 (Proposed in M8).
- [ ] Owner live checks for Google, Microsoft, Jira/ADO (`docs/planning/connectors-setup.md`). Nothing was run against real accounts; all connector tests use the fake cloud.
- [ ] Embeddings (hybrid search) for knowledge collections. They are lexical only today.
- [ ] Chromium for the packaged app: the frozen sidecar bundles the Playwright driver (+103 MB; the sidecar is now 445 MB) and uses the per-user browser cache (`%LOCALAPPDATA%\ms-playwright`). Chromium itself still needs a one-time `playwright install chromium`; the installer could do it, or offer it on first research.
- [ ] Google OAuth apps in "Testing" mode get 7-day refresh tokens: reconnect weekly, or publish the app for personal use.
- [ ] Long Drive/OneDrive index runs: the token is refreshed on a 401 and the request retried once; this is tested per request but not on a real run that outlives the access token.
- [ ] Mail/calendar CLI commands (today they are reachable through chat intents and tools only).
- [ ] Website collections: optional same-site crawling depth (today only the listed pages).
