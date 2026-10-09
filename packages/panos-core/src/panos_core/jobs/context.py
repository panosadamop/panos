"""What a workflow sees while it runs a job (ADR-010/011).

* ``step()`` records a job step *before* the work starts (NFR-REL-001) and its outcome after.
* ``safe_point()`` is where pause, cancel and limits take effect (AC-US052-03, FR-023).
* ``tool()`` runs a tool through the runtime (policy, approvals, audit). When a tool needs
  approval, the request is persisted in the job state and the workflow is suspended; it is
  re-run after the decision and the same call then consumes the approval token.
* ``agent()`` runs a structured agent through the AI gateway with the job's privacy label.
"""

import hashlib
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from panos_core.agents import AgentOutcome, AgentRole, AgentTask, run_agent
from panos_core.ai.gateway import AIGateway, CallContext
from panos_core.checkpoints import CheckpointService
from panos_core.config.settings import JobSettings
from panos_core.domain import Project
from panos_core.domain.enums import JobStatus, PrivacyMode, StepStatus
from panos_core.domain.models import Job, JobStep
from panos_core.jobs.service import JobService
from panos_core.persistence.unit_of_work import UnitOfWorkFactory
from panos_core.repository import AnalysisService
from panos_core.requirements.service import RequirementsService
from panos_core.tools import ToolContext, ToolRuntime
from panos_core.tools.contract import CancelToken
from panos_core.tools.runtime import ToolResult, ToolStatus


class JobContextFactory(Protocol):
    async def __call__(
        self, project: Project | None, *, actor: str, job_id: str, cancel: CancelToken
    ) -> ToolContext: ...


class Suspend(Exception):  # noqa: N818 - control flow, not an error
    """Stop the workflow; the job is already in ``status`` (waiting or paused)."""

    def __init__(self, status: JobStatus, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class Blocked(Exception):  # noqa: N818 - control flow, not an error
    """The job cannot continue without the user (limit reached, repairs exhausted...)."""

    def __init__(self, reason: str, report: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.report = report or {}


class NeedsUser(Exception):  # noqa: N818 - control flow, not an error
    """The job waits for a user decision (WAITING_FOR_USER); resume re-runs the workflow."""

    def __init__(self, reason: str, report: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.report = report or {}


class ApprovalRejectedError(Exception):
    def __init__(self, approval_id: str, tool: str) -> None:
        super().__init__(f"approval {approval_id} for {tool} was rejected")
        self.approval_id = approval_id
        self.tool = tool


@dataclass(frozen=True)
class Limits:
    max_runtime_s: int
    max_cost_usd: float
    max_tokens: int
    max_repair_attempts: int

    @classmethod
    def for_job(cls, job: Job, defaults: JobSettings) -> "Limits":
        b = job.budget
        return cls(
            max_runtime_s=b.max_runtime_s or defaults.max_runtime_s,
            max_cost_usd=b.max_cost_usd if b.max_cost_usd is not None else defaults.max_cost_usd,
            max_tokens=b.max_tokens or defaults.max_tokens,
            max_repair_attempts=b.max_repair_attempts
            if b.max_repair_attempts is not None
            else defaults.max_repair_attempts,
        )


@dataclass
class JobServices:
    jobs: JobService
    tools: ToolRuntime
    gateway: AIGateway
    checkpoints: CheckpointService
    analysis: AnalysisService
    uow_factory: UnitOfWorkFactory
    settings: JobSettings
    context_for: JobContextFactory
    requirements: "RequirementsService | None" = None


@dataclass
class StepHandle:
    step: JobStep
    output: str = ""
    status: StepStatus = StepStatus.SUCCEEDED


def call_key(name: str, payload: dict[str, Any]) -> str:
    raw = json.dumps({"tool": name, "input": payload}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass
class JobContext:
    job: Job
    project: Project | None
    services: JobServices
    cancel: CancelToken
    tokens: dict[str, str]  # approval id → token (in memory only; never persisted)
    on_pause: Callable[[str], Awaitable[None]]
    pause_requested: bool = False
    segment_started: float = field(default_factory=time.monotonic)

    @property
    def state(self) -> dict[str, Any]:
        return self.job.state

    @property
    def limits(self) -> Limits:
        return Limits.for_job(self.job, self.services.settings)

    @property
    def privacy(self) -> PrivacyMode:
        return self.project.privacy_mode if self.project else PrivacyMode.LOCAL_ONLY

    async def save(self) -> None:
        self.state["active_s"] = self.active_seconds()
        self.segment_started = time.monotonic()
        await self.services.jobs.save_state(self.job.id, self.state)

    def active_seconds(self) -> float:
        return float(self.state.get("active_s", 0.0)) + time.monotonic() - self.segment_started

    # ------------------------------------------------------------------ control points
    async def safe_point(self) -> None:
        self.cancel.raise_if_cancelled()
        limits = self.limits
        if self.active_seconds() > limits.max_runtime_s:
            raise Blocked(f"limit reached: runtime over {limits.max_runtime_s}s")
        async with self.services.uow_factory() as uow:
            tokens, cost = await uow.ai_usage.totals_for_job(self.job.id)
        if tokens > limits.max_tokens:
            raise Blocked(f"limit reached: {tokens} AI tokens (max {limits.max_tokens})")
        if cost > limits.max_cost_usd:
            raise Blocked(f"limit reached: AI cost ${cost:.4f} (max ${limits.max_cost_usd:.2f})")
        if self.pause_requested:
            await self.save()
            await self.on_pause(self.job.id)
            raise Suspend(JobStatus.PAUSED, "paused by user")

    @asynccontextmanager
    async def step(
        self, agent: str, summary: str, *, tool: str | None = None
    ) -> AsyncIterator[StepHandle]:
        record = await self.services.jobs.start_step(
            self.job.id, agent=agent, summary=summary, tool=tool
        )
        handle = StepHandle(record)
        try:
            yield handle
        except Suspend:
            await self.services.jobs.finish_step(record, StepStatus.SKIPPED, "suspended")
            raise
        except BaseException as exc:
            status = StepStatus.CANCELLED if self.cancel.cancelled else StepStatus.FAILED
            await self.services.jobs.finish_step(record, status, f"{type(exc).__name__}: {exc}")
            raise
        await self.services.jobs.finish_step(record, handle.status, handle.output)

    # ------------------------------------------------------------------ tools and agents
    async def tool(self, name: str, payload: dict[str, Any], *, actor: str) -> ToolResult:
        key = call_key(name, payload)
        pending = self.state.get("pending_approval")
        token = None
        if pending and pending.get("key") == key:
            approval_id = pending["approval_id"]
            if approval_id in self.state.get("rejected_approvals", []):
                self.state.pop("pending_approval", None)
                await self.save()
                raise ApprovalRejectedError(approval_id, name)
            token = self.tokens.pop(approval_id, None)
        ctx = await self.services.context_for(
            self.project, actor=actor, job_id=self.job.id, cancel=self.cancel
        )
        result = await self.services.tools.invoke(name, payload, ctx, approval_token=token)
        if result.status == ToolStatus.APPROVAL_REQUIRED and result.approval_id:
            self.state["pending_approval"] = {
                "key": key,
                "approval_id": result.approval_id,
                "tool": name,
            }
            await self.save()
            raise Suspend(
                JobStatus.WAITING_FOR_APPROVAL, f"approval {result.approval_id} required for {name}"
            )
        if pending and pending.get("key") == key:
            self.state.pop("pending_approval", None)
            await self.save()
        if result.status == ToolStatus.CANCELLED:
            self.cancel.raise_if_cancelled()
        return result

    async def agent[O: BaseModel](self, role: AgentRole[O], task: AgentTask) -> AgentOutcome[O]:
        ctx = CallContext(
            actor=f"agent:{role.name.lower()}",
            job_id=self.job.id,
            project_id=self.project.id if self.project else None,
        )
        return await run_agent(
            role, task, gateway=self.services.gateway, ctx=ctx, privacy=self.privacy
        )


class Workflow(Protocol):
    kind: str

    async def run(self, ctx: JobContext) -> dict[str, Any]:
        """Run (or resume, from ``ctx.state``) the job; return the final report."""
        ...
