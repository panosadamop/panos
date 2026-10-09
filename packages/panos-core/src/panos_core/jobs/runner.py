"""Asyncio job runner (FR-022, ADR-011, US-050/052).

* Jobs run as asyncio tasks, at most ``jobs.max_concurrent`` at a time.
* **Recovery:** on start, jobs found RUNNING, PLANNING or WAITING_FOR_TOOL were interrupted
  by a crash or shutdown and become PAUSED (reason ``interrupted``); QUEUED jobs start.
* **Pause** takes effect at the workflow's next safe point; **resume** re-runs the workflow,
  which continues from its persisted state.
* **Cancel** fires the job's cancel token (running tool processes are killed with their
  whole process tree, ADR-007) and cancels the task; the job ends CANCELLED with a summary.
* **Approvals:** a decision on a job's approval resumes the job; the approval token stays in
  memory only. A rejection is recorded in the job state so the workflow can react.
* Status changes are published on the event bus and passed to notifications (FR-025).
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from panos_core.domain import Project
from panos_core.domain.enums import JobStatus
from panos_core.domain.errors import DomainError
from panos_core.domain.models import ActionApproval, Job, JobBudget
from panos_core.events import EventBus
from panos_core.jobs.context import (
    ApprovalRejectedError,
    Blocked,
    JobContext,
    JobServices,
    NeedsUser,
    Suspend,
    Workflow,
)
from panos_core.jobs.state_machine import can_transition
from panos_core.observability import bind_context, get_logger
from panos_core.tools.contract import CancelToken

log = get_logger(__name__)

INTERRUPTED: Final = "interrupted"
_INTERRUPTIBLE: Final = (JobStatus.RUNNING, JobStatus.PLANNING, JobStatus.WAITING_FOR_TOOL)
# WAITING_FOR_APPROVAL is resumable so an expired approval request can be re-issued.
_RESUMABLE: Final = frozenset(
    {
        JobStatus.PAUSED,
        JobStatus.BLOCKED,
        JobStatus.QUEUED,
        JobStatus.WAITING_FOR_APPROVAL,
        JobStatus.WAITING_FOR_USER,
    }
)

StatusListener = Callable[[Job], Awaitable[None]]


class JobError(DomainError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class _Running:
    task: asyncio.Task[None]
    ctx: JobContext | None = None
    cancel: CancelToken = field(default_factory=CancelToken)


class JobRunner:
    def __init__(
        self,
        services: JobServices,
        workflows: Sequence[Workflow],
        events: EventBus,
        *,
        project_lookup: Callable[[str], Awaitable[Project]],
        actor: str = "system",
    ) -> None:
        self._s = services
        self._workflows = {w.kind: w for w in workflows}
        self._events = events
        self._project = project_lookup
        self._actor = actor
        self._running: dict[str, _Running] = {}
        self._tokens: dict[str, dict[str, str]] = {}
        self._sem = asyncio.Semaphore(services.settings.max_concurrent)
        self._listeners: list[StatusListener] = []

    # ------------------------------------------------------------------ wiring
    def add_listener(self, listener: StatusListener) -> None:
        self._listeners.append(listener)

    @property
    def services(self) -> JobServices:
        return self._s

    @property
    def kinds(self) -> list[str]:
        return sorted(self._workflows)

    def is_running(self, job_id: str) -> bool:
        return job_id in self._running

    async def _transition(
        self, job_id: str, target: JobStatus, reason: str = "", *, actor: str | None = None
    ) -> Job:
        job = await self._s.jobs.transition(
            job_id, target, actor=actor or self._actor, reason=reason
        )
        await self._announce(job)
        return job

    async def _announce(self, job: Job) -> None:
        self._events.publish(
            "job.status",
            {
                "job_id": job.id,
                "status": job.status.value,
                "reason": job.status_reason,
                "project_id": job.project_id,
                "kind": job.kind,
            },
            job_id=job.id,
        )
        for listener in self._listeners:
            try:
                await listener(job)
            except Exception:
                log.exception("job_listener_failed", job_id=job.id)

    # ------------------------------------------------------------------ lifecycle
    async def recover(self) -> list[str]:
        """Startup recovery (ADR-011). Returns the ids of interrupted jobs."""
        interrupted = []
        for job in await self._s.jobs.list(statuses=list(_INTERRUPTIBLE), limit=1000):
            await self._transition(job.id, JobStatus.PAUSED, INTERRUPTED)
            interrupted.append(job.id)
        for job in await self._s.jobs.list(statuses=[JobStatus.QUEUED], limit=1000):
            self._schedule(job.id)
        if interrupted:
            log.warning("jobs_interrupted", jobs=interrupted)
        return interrupted

    async def submit(
        self,
        objective: str,
        *,
        kind: str = "coding",
        project: Project | None = None,
        input: dict[str, Any] | None = None,
        budget: JobBudget | None = None,
        actor: str = "user",
    ) -> Job:
        if kind not in self._workflows:
            raise JobError("unsupported", f"unknown job kind {kind!r}")
        job = await self._s.jobs.create(
            objective,
            project_id=project.id if project else None,
            budget=budget,
            actor=actor,
            kind=kind,
            input=input,
        )
        await self._announce(job)
        self._schedule(job.id)
        return job

    def _schedule(self, job_id: str) -> None:
        if job_id in self._running:
            return
        cancel = CancelToken()
        task = asyncio.create_task(self._run(job_id, cancel), name=f"job:{job_id}")
        self._running[job_id] = _Running(task=task, cancel=cancel)

    async def pause(self, job_id: str, *, actor: str = "user") -> Job:
        running = self._running.get(job_id)
        if running is not None and running.ctx is not None:
            running.ctx.pause_requested = True  # takes effect at the next safe point
            return await self._s.jobs.get(job_id)
        job = await self._s.jobs.get(job_id)
        if not can_transition(job.status, JobStatus.PAUSED):
            raise JobError("invalid_state", f"cannot pause a {job.status} job")
        if running is not None:  # scheduled but not yet started
            running.cancel.cancel()
            running.task.cancel()
        return await self._transition(job_id, JobStatus.PAUSED, "paused by user", actor=actor)

    async def resume(self, job_id: str, *, actor: str = "user") -> Job:
        job = await self._s.jobs.get(job_id)
        if job.status not in _RESUMABLE:
            raise JobError("invalid_state", f"cannot resume a {job.status} job")
        if job_id in self._running:
            return job
        await self._s.jobs.save_state(job_id, {**job.state, "resumed_by": actor})
        self._schedule(job_id)
        return job

    async def cancel(self, job_id: str, *, actor: str = "user") -> Job:
        job = await self._s.jobs.get(job_id)
        if job.status.is_terminal:
            raise JobError("invalid_state", f"job is already {job.status}")
        running = self._running.get(job_id)
        if running is not None:
            running.cancel.cancel()  # kills tool processes (process tree)
            running.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(running.task), 30)
        job = await self._s.jobs.get(job_id)
        if not job.status.is_terminal:
            await self._finish_cancelled(job, actor)
        return await self._s.jobs.get(job_id)

    async def _finish_cancelled(self, job: Job, actor: str) -> None:
        steps = await self._s.jobs.steps(job.id)
        await self._s.jobs.set_result(
            job.id,
            {
                "success": False,
                "cancelled": True,
                "summary": f"Cancelled by {actor} after {len(steps)} step(s).",
                "phase": job.state.get("phase"),
            },
        )
        await self._transition(job.id, JobStatus.CANCELLED, f"cancelled by {actor}", actor=actor)

    async def on_approval(self, approval: ActionApproval, token: str | None) -> None:
        """Resume a job waiting for ``approval`` (approved → token; rejected → recorded)."""
        if approval.job_id is None:
            return
        job = await self._s.jobs.get(approval.job_id)
        if job.status != JobStatus.WAITING_FOR_APPROVAL:
            if token:
                self._tokens.setdefault(job.id, {})[approval.id] = token
            return
        if token:
            self._tokens.setdefault(job.id, {})[approval.id] = token
        else:
            rejected = [*job.state.get("rejected_approvals", []), approval.id]
            await self._s.jobs.save_state(job.id, {**job.state, "rejected_approvals": rejected})
        self._schedule(job.id)

    async def close(self) -> None:
        """Shutdown: stop tasks; their jobs stay RUNNING and are recovered as interrupted."""
        for running in list(self._running.values()):
            running.cancel.cancel()
            running.task.cancel()
        for running in list(self._running.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await running.task

    # ------------------------------------------------------------------ execution
    async def _run(self, job_id: str, cancel: CancelToken) -> None:
        """Task body. On cancellation nothing is written here: ``cancel()`` finalizes a
        user cancel, and a shutdown leaves the job for startup recovery."""
        bind_context(job_id=job_id)
        try:
            async with self._sem:
                await self._execute(job_id, cancel)
        except asyncio.CancelledError:
            log.info("job_task_cancelled", job_id=job_id)
        finally:
            self._running.pop(job_id, None)

    async def _execute(self, job_id: str, cancel: CancelToken) -> None:
        job = await self._s.jobs.get(job_id)
        workflow = self._workflows.get(job.kind)
        if workflow is None:
            await self._transition(job_id, JobStatus.FAILED, f"unknown job kind {job.kind}")
            return
        if job.status != JobStatus.RUNNING:
            job = await self._transition(job_id, JobStatus.RUNNING, "started")
        project = await self._project(job.project_id) if job.project_id else None
        ctx = JobContext(
            job=job,
            project=project,
            services=self._s,
            cancel=cancel,
            tokens=self._tokens.setdefault(job_id, {}),
            on_pause=lambda jid: self._pause_now(jid),
        )
        self._running[job_id].ctx = ctx
        try:
            report = await workflow.run(ctx)
        except Suspend as s:
            if s.status == JobStatus.WAITING_FOR_APPROVAL:
                await self._announce(await self._s.jobs.get(job_id))
            return
        except Blocked as b:
            await ctx.save()
            await self._s.jobs.set_result(
                job_id, {"success": False, "blocked": True, "reason": b.reason, **b.report}
            )
            await self._transition(job_id, JobStatus.BLOCKED, b.reason)
            return
        except NeedsUser as n:
            await ctx.save()
            await self._s.jobs.set_result(
                job_id, {"success": False, "waiting": True, "reason": n.reason, **n.report}
            )
            await self._transition(job_id, JobStatus.WAITING_FOR_USER, n.reason)
            return
        except ApprovalRejectedError as exc:
            await ctx.save()
            await self._s.jobs.set_result(job_id, {"success": False, "reason": str(exc)})
            await self._transition(job_id, JobStatus.BLOCKED, str(exc))
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if cancel.cancelled:
                raise asyncio.CancelledError from exc
            log.exception("job_failed", job_id=job_id)
            await self._s.jobs.set_result(
                job_id,
                {
                    "success": False,
                    "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                    "phase": ctx.state.get("phase"),
                },
            )
            await self._transition(job_id, JobStatus.FAILED, f"{type(exc).__name__}: {exc}"[:300])
            return
        await ctx.save()
        await self._s.jobs.set_result(job_id, report)
        if report.get("success") is False:
            # Never report failure as success (NFR-REL-004).
            await self._transition(
                job_id, JobStatus.BLOCKED, str(report.get("reason", "unsuccessful"))
            )
        else:
            await self._transition(job_id, JobStatus.COMPLETED, "completed")

    async def _pause_now(self, job_id: str) -> None:
        await self._transition(job_id, JobStatus.PAUSED, "paused by user", actor="user")
