"""Coding workflow (FR-006, US-020/022, AC-US020-01..04, BR-011).

understand → analyze (fresh snapshot) → plan (Planner) → checkpoint → implement (Developer,
structured edits through ``fs.*`` tools) → verify (discovered format/lint/type-check/build/
test commands) → bounded repair loop (QA diagnoses, Developer fixes; at most N attempts,
then BLOCKED) → review (Reviewer) → trace check → report → commit (approval requested; the
commit runs only with approval of the exact tree, never automatically).

Phases are recorded in the job state, so a paused or interrupted job resumes after the last
completed phase. An interrupted *implement* phase is rolled back to the checkpoint first,
so edits are never applied twice.
"""

import json
from pathlib import Path
from typing import Any, Final

from panos_core.agents import (
    DEVELOPER,
    PLANNER,
    QA,
    REVIEWER,
    AgentError,
    AgentTask,
    ChangeSet,
    Diagnosis,
    FileEdit,
    Handoff,
    Plan,
    Review,
)
from panos_core.ai.context import RetrievedItem
from panos_core.domain import Project
from panos_core.domain.enums import StepStatus
from panos_core.domain.models import RepositorySnapshot
from panos_core.jobs.context import ApprovalRejectedError, Blocked, JobContext
from panos_core.repository import gitcli
from panos_core.tools.runtime import ToolStatus

PHASES: Final = (
    "analyze",
    "plan",
    "checkpoint",
    "implement",
    "verify",
    "review",
    "report",
    "commit",
)
VERIFY_ORDER: Final = ("format", "lint", "typecheck", "build", "test")
MAX_FILES: Final = 12
MAX_CONTEXT_CHARS: Final = 60_000
OUTPUT_TAIL: Final = 4000
DEV = "agent:developer"
QA_ACTOR = "agent:qa"


class EditError(Exception):
    pass


def _project(ctx: JobContext) -> Project:
    if ctx.project is None:
        raise Blocked("a coding job needs a project")
    return ctx.project


def _tail(text: str, limit: int = OUTPUT_TAIL) -> str:
    return text if len(text) <= limit else "..." + text[-limit:]


class CodingWorkflow:
    kind = "coding"

    async def run(self, ctx: JobContext) -> dict[str, Any]:
        _project(ctx)
        done: list[str] = ctx.state.setdefault("phases_done", [])
        for phase in PHASES:
            if phase in done:
                continue
            await ctx.safe_point()
            ctx.state["phase"] = phase
            await ctx.save()
            try:
                await getattr(self, f"_{phase}")(ctx)
            except EditError as exc:
                raise Blocked(
                    f"could not apply the proposed edits: {exc}", {"phase": phase}
                ) from exc
            except AgentError as exc:
                # Resumable (BLOCKED), unlike FAILED: the user can retry or roll back.
                raise Blocked(
                    f"the model returned unusable output: {exc}", {"phase": phase}
                ) from exc
            done.append(phase)
            await ctx.save()
        return dict(ctx.state["report"])

    # ------------------------------------------------------------------ phases
    async def _analyze(self, ctx: JobContext) -> None:
        project = _project(ctx)
        async with ctx.step("orchestrator", "Analyze repository (fresh snapshot)") as step:
            snapshot = await ctx.services.analysis.analyze(project, actor="agent:planner")
            ctx.state["snapshot_id"] = snapshot.id
            step.output = (
                f"stack={snapshot.stack.get('frameworks', [])} commands={sorted(snapshot.commands)}"
            )

    async def _snapshot(self, ctx: JobContext) -> RepositorySnapshot:
        project = _project(ctx)
        snapshot = await ctx.services.analysis.latest(project.id)
        if snapshot is None:
            raise Blocked("repository snapshot missing")
        return snapshot

    async def _plan(self, ctx: JobContext) -> None:
        snapshot = await self._snapshot(ctx)
        overview = {
            "stack": snapshot.stack,
            "commands": snapshot.commands,
            "tree": snapshot.structure.get("tree"),
            "outline": snapshot.structure.get("outline"),
            "conventions": snapshot.structure.get("conventions"),
        }
        task = AgentTask(
            objective=ctx.job.objective,
            facts=[
                f"Repository facts (from analysis): {json.dumps(overview, default=str)[:12000]}"
            ],
            permissions=["fs.read", "fs.write", "fs.delete", "shell.run (discovered commands)"],
        )
        async with ctx.step("agent:planner", "Plan the implementation") as step:
            outcome = await ctx.agent(PLANNER, task)
            ctx.state["plan"] = outcome.result.model_dump()
            ctx.state["handoff"] = outcome.handoff.model_dump()
            step.output = outcome.result.summary

    async def _checkpoint(self, ctx: JobContext) -> None:
        project = _project(ctx)
        async with ctx.step("orchestrator", "Checkpoint before changes") as step:
            cp = await ctx.services.checkpoints.create(
                project, job_id=ctx.job.id, actor="system", label="before changes"
            )
            ctx.state["checkpoint_id"] = cp.id
            step.output = cp.reference

    async def _implement(self, ctx: JobContext) -> None:
        project = _project(ctx)
        if ctx.state.get("implement_started"):
            # Interrupted mid-way: restore the checkpoint so edits are not applied twice.
            checkpoint = await ctx.services.checkpoints.get(ctx.state["checkpoint_id"])
            await ctx.services.checkpoints.rollback(project, checkpoint, actor="system")
        ctx.state["implement_started"] = True
        await ctx.save()
        plan = Plan.model_validate(ctx.state["plan"])
        files = list(
            dict.fromkeys([*plan.files_to_read, *(f for s in plan.steps for f in s.files)])
        )
        context = await self._read_files(ctx, files)
        task = AgentTask(
            objective=ctx.job.objective,
            handoff=Handoff.model_validate(ctx.state.get("handoff", {})),
            context=context,
            inputs={"Plan": plan.model_dump()},
        )
        async with ctx.step("agent:developer", "Implement the plan") as step:
            outcome = await ctx.agent(DEVELOPER, task)
            changes = outcome.result
            applied = await self._apply(ctx, changes.edits)
            ctx.state["change_summary"] = changes.summary
            ctx.state["commit_message"] = changes.commit_message
            ctx.state["handoff"] = outcome.handoff.model_dump()
            step.output = f"{changes.summary} ({len(applied)} edit(s))"

    async def _verify(self, ctx: JobContext) -> None:
        snapshot = await self._snapshot(ctx)
        commands = [
            (kind, argv) for kind in VERIFY_ORDER for argv in snapshot.commands.get(kind, [])
        ]
        ctx.state["verification_available"] = bool(commands)
        results = await self._run_commands(ctx, commands, label="Verify")
        attempts = int(ctx.state.get("repair_attempts", 0))
        while failed := [r for r in results if not r["ok"]]:
            if attempts >= ctx.limits.max_repair_attempts:
                diagnosis = ctx.state.get("last_diagnosis", {})
                raise Blocked(
                    f"verification still failing after {attempts} repair attempt(s)",
                    {"failures": failed, "diagnosis": diagnosis, "repair_attempts": attempts},
                )
            attempts += 1
            ctx.state["repair_attempts"] = attempts
            await ctx.save()
            await ctx.safe_point()
            async with ctx.step("orchestrator", f"Repair attempt {attempts}") as step:
                try:
                    await self._repair(ctx, failed, attempts)
                except (EditError, AgentError) as exc:
                    # A fix that cannot be applied is a failed attempt, fed back next time.
                    failed = [
                        *failed,
                        {
                            "kind": "edit",
                            "argv": ["(apply fix)"],
                            "ok": False,
                            "exit_code": None,
                            "output": str(exc),
                        },
                    ]
                    step.output = f"fix not applied: {exc}"[:500]
                    step.status = StepStatus.FAILED
                    results = failed
                    continue
                results = await self._run_commands(ctx, commands, label=f"Re-verify #{attempts}")
                remaining = sum(1 for r in results if not r["ok"])
                step.output = "fixed" if not remaining else f"{remaining} check(s) still failing"
        ctx.state["verification"] = results

    async def _repair(self, ctx: JobContext, failed: list[dict[str, Any]], attempt: int) -> None:
        failure_text = "\n\n".join(
            f"$ {' '.join(r['argv'])} (exit {r['exit_code']})\n{r['output']}" for r in failed
        )
        changed = [c.path for c in await self._changes(ctx)]
        context = await self._read_files(ctx, changed)
        failure_item = RetrievedItem(
            text=failure_text, source="verification-output", privacy=ctx.privacy
        )
        diagnosis_task = AgentTask(
            objective=ctx.job.objective,
            context=[failure_item, *context],
            handoff=Handoff.model_validate(ctx.state.get("handoff", {})),
        )
        async with ctx.step("agent:qa", f"Diagnose failures (attempt {attempt})") as step:
            diagnosis = (await ctx.agent(QA, diagnosis_task)).result
            ctx.state["last_diagnosis"] = diagnosis.model_dump()
            step.output = diagnosis.summary
        extra = await self._read_files(ctx, [f for f in diagnosis.files if f not in changed])
        fix_task = AgentTask(
            objective=f"Fix the failing checks for: {ctx.job.objective}",
            context=[failure_item, *context, *extra],
            handoff=Handoff.model_validate(ctx.state.get("handoff", {})),
            inputs={"Diagnosis": diagnosis.model_dump()},
        )
        async with ctx.step("agent:developer", f"Apply fix (attempt {attempt})") as step:
            fix = (await ctx.agent(DEVELOPER, fix_task)).result
            applied = await self._apply(ctx, fix.edits)
            step.output = f"{fix.summary} ({len(applied)} edit(s))"

    async def _review(self, ctx: JobContext) -> None:
        project = _project(ctx)
        checkpoint = await ctx.services.checkpoints.get(ctx.state["checkpoint_id"])
        diff = await ctx.services.checkpoints.diff(project, checkpoint)
        task = AgentTask(
            objective=ctx.job.objective,
            context=[
                RetrievedItem(text=diff or "(no changes)", source="job-diff", privacy=ctx.privacy)
            ],
            handoff=Handoff.model_validate(ctx.state.get("handoff", {})),
            inputs={
                "Verification": [
                    {k: r[k] for k in ("kind", "argv", "ok")}
                    for r in ctx.state.get("verification", [])
                ]
            },
        )
        async with ctx.step("agent:reviewer", "Independent review") as step:
            review = (await ctx.agent(REVIEWER, task)).result
            ctx.state["review"] = review.model_dump()
            step.output = f"{review.verdict}: {review.summary}"
        ctx.state["diff_chars"] = len(diff)

    async def _report(self, ctx: JobContext) -> None:
        changes = await self._changes(ctx)
        review = Review.model_validate(ctx.state["review"])
        handoff = Handoff.model_validate(ctx.state.get("handoff", {}))
        unresolved = [*handoff.open_questions]
        if not ctx.state.get("verification_available"):
            unresolved.append(
                "no format/lint/test commands were discovered: changes are unverified"
            )
        unresolved += [
            f"review ({f.severity}): {f.path} {f.message}".strip()
            for f in review.findings
            if f.severity in ("major", "critical")
        ]
        trace = self._trace(handoff, changes, ctx)
        ctx.state["report"] = {
            "success": True,
            "summary": ctx.state.get("change_summary", ""),
            "plan": Plan.model_validate(ctx.state["plan"]).summary,
            "checkpoint_id": ctx.state.get("checkpoint_id"),
            "changed_files": [{"status": c.status, "path": c.path} for c in changes],
            "verification": [
                {k: r[k] for k in ("kind", "argv", "ok", "exit_code")}
                for r in ctx.state.get("verification", [])
            ],
            "repair_attempts": ctx.state.get("repair_attempts", 0),
            "review": {
                "verdict": review.verdict,
                "summary": review.summary,
                "findings": [f.model_dump() for f in review.findings],
            },
            "traceability": trace,
            "decisions": handoff.decisions,
            "assumptions": handoff.assumptions,
            "unresolved": unresolved,
        }

    async def _commit(self, ctx: JobContext) -> None:
        project = _project(ctx)
        report = ctx.state["report"]
        paths = [c["path"] for c in report["changed_files"]]
        wants_commit = bool(ctx.job.input.get("commit", True))
        if not (wants_commit and paths and await gitcli.is_repository(Path(project.path))):
            report["commit"] = {"status": "skipped"}
            return
        message = ctx.state.get("commit_message") or ctx.job.objective.splitlines()[0][:72]
        payload = {"message": message, "paths": paths}
        try:
            async with ctx.step(DEV, "Commit (requires approval)", tool="git.commit") as step:
                result = await ctx.tool("git.commit", payload, actor=DEV)
                step.output = result.status.value
        except ApprovalRejectedError:
            report["commit"] = {"status": "rejected"}
            report["unresolved"].append("commit was not approved; changes are uncommitted")
            return
        if result.status == ToolStatus.SUCCEEDED and result.output:
            report["commit"] = {"status": "committed", **result.output}
        else:
            report["commit"] = {"status": result.status.value.lower(), "error": result.error}
            report["unresolved"].append(f"commit not created: {result.error}")

    # ------------------------------------------------------------------ helpers
    async def _changes(self, ctx: JobContext) -> list[Any]:
        project = _project(ctx)
        checkpoint = await ctx.services.checkpoints.get(ctx.state["checkpoint_id"])
        return await ctx.services.checkpoints.changes(project, checkpoint)

    async def _read_files(self, ctx: JobContext, paths: list[str]) -> list[RetrievedItem]:
        items: list[RetrievedItem] = []
        budget = MAX_CONTEXT_CHARS
        for path in paths[:MAX_FILES]:
            result = await ctx.tool("fs.read", {"path": path}, actor=DEV)
            if result.status != ToolStatus.SUCCEEDED or not result.output:
                continue
            out = result.output
            if out.get("binary"):
                continue
            content = str(out["content"])[:budget]
            budget -= len(content)
            items.append(RetrievedItem(text=content, source=str(out["path"]), privacy=ctx.privacy))
            if budget <= 0:
                break
        return items

    async def _apply(self, ctx: JobContext, edits: list[FileEdit]) -> list[str]:
        applied: list[str] = []
        for edit in edits:
            await ctx.safe_point()
            if edit.action == "delete":
                result = await ctx.tool("fs.delete", {"path": edit.path}, actor=DEV)
            elif edit.action in ("create", "replace"):
                result = await ctx.tool(
                    "fs.write", {"path": edit.path, "content": edit.content or ""}, actor=DEV
                )
            else:
                current = await ctx.tool("fs.read", {"path": edit.path}, actor=DEV)
                if current.status != ToolStatus.SUCCEEDED or not current.output:
                    raise EditError(f"cannot read {edit.path}: {current.error}")
                text = str(current.output["content"])
                search, replace = edit.search or "", edit.replace or ""
                if not search or text.count(search) != 1:
                    raise EditError(
                        f"edit for {edit.path}: search text must occur exactly "
                        f"once (found {text.count(search) if search else 0})"
                    )
                result = await ctx.tool(
                    "fs.write",
                    {
                        "path": edit.path,
                        "content": text.replace(search, replace, 1),
                        "expected_sha256": current.output["sha256"],
                    },
                    actor=DEV,
                )
            if result.status != ToolStatus.SUCCEEDED:
                raise EditError(f"{edit.action} {edit.path}: {result.error or result.status}")
            applied.append(edit.path)
        return applied

    async def _run_commands(
        self, ctx: JobContext, commands: list[tuple[str, list[str]]], *, label: str
    ) -> list[dict[str, Any]]:
        results = []
        for kind, argv in commands:
            await ctx.safe_point()
            async with ctx.step(QA_ACTOR, f"{label}: {kind}", tool="shell.run") as step:
                result = await ctx.tool(
                    "shell.run", {"argv": argv, "timeout_s": 1800}, actor=QA_ACTOR
                )
                out = result.output or {}
                exit_code = out.get("exit_code")
                ok = result.status == ToolStatus.SUCCEEDED and exit_code == 0
                text = _tail(
                    f"{out.get('stdout', '')}\n{out.get('stderr', '')}".strip()
                    or (result.error or "")
                )
                results.append(
                    {"kind": kind, "argv": argv, "ok": ok, "exit_code": exit_code, "output": text}
                )
                step.output = f"exit {exit_code}" if result.output else str(result.error)
        return results

    @staticmethod
    def _trace(handoff: Handoff, changes: list[Any], ctx: JobContext) -> list[dict[str, Any]]:
        """Requirement IDs carried by the job and whether the change mentions them."""
        plan_text = json.dumps(ctx.state.get("plan", {}))
        return [
            {"id": rid, "in_plan": rid in plan_text, "files_changed": len(changes)}
            for rid in handoff.requirement_ids
        ]


__all__ = ["ChangeSet", "CodingWorkflow", "Diagnosis", "EditError"]
