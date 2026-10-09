# ADR-007: Controlled shell execution

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** FR-012, FR-013, FR-014, BR-018, NFR-SEC-002, SECURITY (approval bypass)

## Context
A shell tool that accepts free-form strings is equivalent to unrestricted shell access and lets the model bypass Git/Docker approvals.

## Decision
**Execution mode**
- Commands are **argv lists executed without a shell** (`subprocess` with `shell=False`).
- Shell profiles (powershell, pwsh, bash, zsh, WSL) exist only for user-initiated scripts, at ≥ P2.

**Classification**
- Every command, including each segment of a shell script, is parsed and classified into action classes: `git.read`, `git.mutate.local`, `git.mutate.shared`, `docker.inspect`, `docker.exec`, `k8s.mutate`, `pkg.install`, `net.upload`, `fs.delete`, `build.test`, `unknown`, …
- Parsers: POSIX via `bashlex`; PowerShell via its AST parser (`[System.Management.Automation.Language.Parser]::ParseInput`); `cmd.exe` is treated conservatively.
- The decision uses the highest-risk class found.
- Unparseable input, or nested interpreters (`bash -c`, `pwsh -EncodedCommand`, `cmd /c`, `python -c`), are classified as `unknown`, which is P3.
- Commands discovered by repository analysis (build/test/lint) are allowlisted as `build.test` (P1) for that project.

**Execution limits**
- Env scrubbing removes secret-looking variables unless allowlisted.
- Cwd is confined to the project.
- Configurable timeout; cancellation kills the process tree (Windows Job Objects / psutil; POSIX process groups).
- Stdout/stderr capped and streamed; exit code, duration, argv and cwd are recorded.

## Alternatives considered
Shell strings with a denylist (bypassable); container-only execution (too heavy for MVP; may be added later as an isolation option).

## Consequences
Some legitimate compound commands will require approval. Accepted residual risk: allowlisted build/test commands execute repository code (e.g. `npm test`). Mitigation: checkpoints, project scoping and approval for unknown commands. Covered by the M2-11 bypass security suite.

## Implementation notes (M2, 2026-10-08)

- **PowerShell parsing without `-EncodedCommand`.** The first implementation passed the parser program via `-EncodedCommand`. Microsoft Defender on the reference machine flagged related command lines (threat 2147948670), because encoded PowerShell is a common malware signal. The parser now receives a fixed one-line program on stdin (`powershell -NoProfile -NonInteractive -Command -`), with the analysed script embedded as base64 *data*. The analysed script is never executed.
- **Executable resolution never consults the current directory.** Bare names are searched on `PATH` only. Names with a separator must resolve inside the project (`./gradlew`) or be absolute. The resolved absolute path is passed to `CreateProcess`/`exec` and bound into the approval payload. If resolution changes between approval and execution, execution aborts. Children get `NoDefaultCurrentDirectoryInExePath=1` on Windows.
- **Batch files (CVE-2024-24576 "BatBadBut").** Arguments containing `& | < > ^ % ! "` or newlines are refused when the target is a `.bat`/`.cmd` file, because `cmd.exe` re-parses them.
- **Process tree.** Windows uses a Job Object (`KILL_ON_JOB_CLOSE`), with a psutil tree-kill fallback. There is a small window between process creation and job assignment in which a grandchild could escape the job; the psutil fallback covers kills after that point. POSIX uses a new session and `killpg`.
- **Action classes** added to the default policy for shell classification: `shell.read` (P0), `build.test` (P1), `shell.write` (P2), `shell.delete` (P3), `shell.unknown` (P3), `net.fetch` (P2), `net.upload` (P3), `git.mutate.local` (P1), `git.destructive` (P3).
