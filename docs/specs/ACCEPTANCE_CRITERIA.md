# Acceptance Criteria

> **Amendments (2026-10-08, approved plan D-11):**
> - Bullets are numbered `AC-USxxx-NN` as prescribed by REQUIREMENTS_PIPELINE.md. Original criteria keep their original order and wording.
> - Criteria marked *(new)* were added for stories that had none. These are drafts accepted with the plan and may be refined when the owning milestone starts; changes are recorded here.

## AC-US001 Wake word
- AC-US001-01 Wake-word detection works without internet.
- AC-US001-02 Ambient audio is not sent to cloud providers.
- AC-US001-03 Detection starts a command utterance session and gives visible feedback.
- AC-US001-04 *(new)* Wake-word sensitivity is configurable and wake-word listening can be temporarily disabled/muted; while muted, no audio is processed for wake-word detection.
- AC-US001-05 *(new)* Ambient and utterance audio are not written to disk unless the user has explicitly enabled audio history/diagnostics.

## AC-US002 Global hotkey
- AC-US002-01 *(new)* A configurable global hotkey starts a command utterance session (push-to-talk or toggle, configurable) while the desktop app is running, including when it is not focused.
- AC-US002-02 *(new)* Hotkey conflicts or registration failures are reported to the user and an alternative is suggested; the app keeps working without the hotkey.
- AC-US002-03 *(new)* Where the platform cannot support global hotkeys (e.g. some Wayland sessions), the limitation is shown in settings.

## AC-US003 Bilingual speech
- AC-US003-01 *(new)* Spoken Greek, English and mixed Greek/English utterances are transcribed without the user selecting a language in advance.
- AC-US003-02 *(new)* English technical terms inside Greek sentences (e.g. framework, tool and command names) are preserved in a usable form in the transcript.
- AC-US003-03 *(new)* The transcript is shown in the UI before or while the command is processed, so recognition errors are visible.
- AC-US003-04 *(new)* Panos responds in the language of the user's request unless configured otherwise.

## AC-US004 Stop speech
- AC-US004-01 *(new)* Saying `stop talking` (or pressing the mute-speech control) stops TTS output within 500 ms.
- AC-US004-02 *(new)* Stopping speech does not change the state of any running job.
- AC-US004-03 *(new)* `stop`, `pause` and `resume` are routed to job control, not to TTS control.

## AC-US005 Local-only projects
- AC-US005-01 *(new)* A project or folder can be marked local-only; newly created or registered projects are local-only by default.
- AC-US005-02 *(new)* While a request involves local-only content (files, retrieval chunks, tool output, screenshots, transcripts or summaries/embeddings derived from them), no cloud AI provider receives any request for that job or conversation.
- AC-US005-03 *(new)* A blocked cloud route is enforced both in routing and at the cloud adapter boundary, and each blocked attempt creates an audit event.
- AC-US005-04 *(new)* If no eligible local model can handle the request, Panos says so instead of silently using a cloud model.
- AC-US005-05 *(new)* Changing a project's privacy mode requires an explicit user action and is audited.

## AC-US010 Open applications
- AC-US010-01 *(new)* Panos lists discovered supported IDEs and browsers, including JetBrains products, VS Code, Sublime Text and installed browsers.
- AC-US010-02 *(new)* The user can open an application by name or configured alias, and a project in its preferred IDE.
- AC-US010-03 *(new)* An application that is not installed or not supported produces a clear message, not a failure.
- AC-US010-04 *(new)* Launches use native APIs or application CLIs, not mouse or keyboard automation.

## AC-US011 Register project
- AC-US011-01 *(new)* A project can be created in `~/Panos/Projects` or registered from any accessible path.
- AC-US011-02 *(new)* Registration stores the privacy mode, preferred IDE, AI policy and knowledge settings.
- AC-US011-03 *(new)* Registering a path that does not exist, is not accessible, or is already registered is rejected with a clear reason.

## AC-US012 Repository analysis
- AC-US012-01 Panos detects primary languages/frameworks and build/test commands.
- AC-US012-02 It records a repository snapshot before material modification.
- AC-US012-03 Failure to understand critical project structure causes clarification rather than blind modification.

## AC-US013 Screen inspection
- AC-US013-01 *(new)* The active window is captured only after an explicit user request or an approved workflow step.
- AC-US013-02 *(new)* The screenshot is kept in memory only and discarded after analysis unless the user explicitly saves it or diagnostics retention is enabled.
- AC-US013-03 *(new)* Screenshots are analyzed by a local vision model by default; a cloud model is used only with explicit per-request consent and when policy allows.
- AC-US013-04 *(new)* The analysis identifies visible errors and suggests next steps; any suggested action still goes through normal tool policy.

## AC-US020 Feature implementation
- AC-US020-01 A non-trivial feature receives an implementation plan.
- AC-US020-02 A checkpoint is created before substantial changes.
- AC-US020-03 Applicable formatting/lint/build/tests execute after implementation.
- AC-US020-04 Panos reports changed files, tests and unresolved issues.

## AC-US021 Requested stack
- AC-US021-01 *(new)* When the user explicitly names a stack, the generated architecture and scaffold use it; deviations require user approval.
- AC-US021-02 *(new)* When no stack is named, Panos proposes one with rationale and does not scaffold before approval.

## AC-US022 Bounded repair
- AC-US022-01 *(new)* Failed build/test output is diagnosed and a fix is attempted automatically up to the configured repair limit.
- AC-US022-02 *(new)* When the limit is reached, the job becomes `BLOCKED` with a diagnosis report and is never reported as successful.
- AC-US022-03 *(new)* Each repair attempt is recorded as a job step with its outcome.

## AC-US023 Rollback
- AC-US023-01 *(new)* The user can roll back a job's project modifications to the checkpoint taken before them.
- AC-US023-02 *(new)* Rollback restores the checkpointed file contents exactly and does not modify the user's Git history, index or stash.
- AC-US023-03 *(new)* Rollback works for projects that are not Git repositories and is audited.

## AC-US024 Offline operation
- AC-US024-01 *(new)* With networking disabled and suitable local models installed, wake word, STT, TTS, conversation, repository analysis, requirements generation and coding workflows work.
- AC-US024-02 *(new)* Offline state is detected and routing selects only local models without error loops.
- AC-US024-03 *(new)* Features that need the internet report that clearly when offline.

## AC-US030 Brief interview
- AC-US030-01 *(new)* Panos identifies ambiguities and missing information in a brief and asks targeted questions.
- AC-US030-02 *(new)* Interview state persists and can be resumed after restart.
- AC-US030-03 *(new)* Answers are recorded with provenance and reflected in the generated requirements.

## AC-US031 Hierarchical approval
- AC-US031-01 Parent approval can cascade to descendants.
- AC-US031-02 User can exclude selected descendants.
- AC-US031-03 Approval records include version, user decision and timestamp.
- AC-US031-04 Modifying an approved item creates a new version requiring appropriate reapproval.

## AC-US032 Traceability
- AC-US032-01 *(new)* For a given business requirement, Panos shows linked functional requirements, backlog items, acceptance criteria, API/data elements, test cases and implementation evidence.
- AC-US032-02 *(new)* Items with no downstream links (e.g. a story without tests) are reported as gaps.
- AC-US032-03 *(new)* Changing an approved upstream item marks affected downstream items as stale.

## AC-US033 Claude skills
- AC-US033-01 *(new)* A full project generates project-specific Claude skills containing only approved project facts and standards.
- AC-US033-02 *(new)* When approved architecture or standards change, the skills are regenerated with a new version.

## AC-US040 Git commit approval
- AC-US040-01 Panos shows a meaningful change summary/diff context before requesting approval.
- AC-US040-02 No commit is created without valid approval for the exact proposed action.

## AC-US041 Docker approval
- AC-US041-01 Panos may inspect Docker configuration without approval.
- AC-US041-02 Any Docker execution request enters WAITING_FOR_APPROVAL.
- AC-US041-03 Rejected approval causes no Docker command to execute.

## AC-US042 Jira/Azure DevOps approval
- AC-US042-01 *(new)* Panos can read work items and prepare proposed changes without approval.
- AC-US042-02 *(new)* No external write happens without approval of the exact prepared change set.

## AC-US043 Email
- AC-US043-01 Read/search/summarize/draft are available.
- AC-US043-02 Default tool registry contains no email-send capability.
- AC-US043-03 Prompt injection cannot cause email sending.

## AC-US044 Calendar
- AC-US044-01 *(new)* Panos reads schedules and free/busy information and reports conflicts.
- AC-US044-02 *(new)* Create/update/delete/respond operations are prepared and executed only after approval of the exact operation.

## AC-US045 Browser boundary
- AC-US045-01 *(new)* Panos can open, navigate and read pages for research and cites its sources.
- AC-US045-02 *(new)* Consequential form submissions, transactional form completion and uploads are not available to autonomous execution.
- AC-US045-03 *(new)* Instructions found in page content cannot change Panos policy or trigger tools on their own authority.

## AC-US050 Autonomous work
- AC-US050-01 Job state survives UI restart.
- AC-US050-02 Limits exist for runtime, AI cost/tokens and repair attempts.
- AC-US050-03 Approval/blocker transitions notify the user.

## AC-US051 Notifications
- AC-US051-01 *(new)* Approvals, blockers, business decisions and completion produce a desktop notification, plus a spoken one when voice notifications are enabled.
- AC-US051-02 *(new)* Routine progress is not narrated by voice.

## AC-US052 Job control
- AC-US052-01 *(new)* The user can pause, resume and cancel a job from CLI, desktop and voice.
- AC-US052-02 *(new)* Cancel stops running tool processes (including child processes) and leaves the job `CANCELLED` with a summary.
- AC-US052-03 *(new)* Pause takes effect at the next safe point; resume continues from the persisted state.

## AC-US060 Node overview *(later phase)*
- AC-US060-01 *(new)* The user sees all authorized nodes with platform, capabilities, status and last-seen time.

## AC-US061 Remote task
- AC-US061-01 Only authenticated authorized nodes are targetable.
- AC-US061-02 User can see target node before approval/execution.
- AC-US061-03 Remote task actions remain subject to target-node permissions.

## AC-US062 Web dashboard *(later phase)*
- AC-US062-01 *(new)* The dashboard shows nodes, projects, jobs, approvals, logs, AI usage/cost and traceability.
- AC-US062-02 *(new)* The dashboard cannot access node files directly or execute arbitrary commands.
