"""Tool runtime: the only path from a tool request to execution (FR-011, NFR-SEC-002).

Pipeline:
    lookup → platform check → input schema validation → plan (target + action class)
    → capability check → policy evaluation → approval (request or consume)
    → persist job step → execute (timeout / cancellation) → output validation → audit

Every non-P0 decision and every non-read execution is audited (FR-028). Job steps are
persisted before execution (NFR-REL-001). Tool output is marked untrusted: it is data,
never instructions (TOOLS.md, ADR-023).
"""

import asyncio
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from panos_core.approvals import ApprovalError, ApprovalService
from panos_core.audit import AuditService
from panos_core.domain.clock import utc_now
from panos_core.domain.enums import JobStatus, PermissionLevel, PolicyDecision, StepStatus
from panos_core.domain.models import JobStep
from panos_core.jobs import JobService, can_transition
from panos_core.nodes import current_platform
from panos_core.observability import bind_context, get_logger
from panos_core.persistence.unit_of_work import UnitOfWorkFactory
from panos_core.policy import PolicyEngine, PolicyRequest, PolicyResult
from panos_core.redaction import redact
from panos_core.tools.contract import (
    ActionPlan,
    CancelledByUserError,
    ExecutionPermit,
    PlanRejectedError,
    SideEffect,
    Tool,
    ToolContext,
    ToolExecutionError,
)
from panos_core.tools.registry import ToolRegistry

log = get_logger(__name__)

_SUMMARY_LIMIT = 2000


class ToolStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DENIED = "DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    INVALID_INPUT = "INVALID_INPUT"
    UNSUPPORTED = "UNSUPPORTED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool: str
    status: ToolStatus
    action: str | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    decision: PolicyResult | None = None
    approval_id: str | None = None
    step_id: str | None = None
    # Tool output is untrusted data and must be kept separate from instructions.
    untrusted_output: bool = True


def _truncate(text: str) -> str:
    return text if len(text) <= _SUMMARY_LIMIT else text[: _SUMMARY_LIMIT - 3] + "..."


class ToolRuntime:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: PolicyEngine,
        approvals: ApprovalService,
        audit: AuditService,
        jobs: JobService,
        uow_factory: UnitOfWorkFactory,
        platform: str | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._approvals = approvals
        self._audit = audit
        self._jobs = jobs
        self._uow_factory = uow_factory
        self._platform = platform or current_platform()

    async def invoke(
        self,
        name: str,
        raw_input: Mapping[str, Any],
        ctx: ToolContext,
        *,
        approval_token: str | None = None,
    ) -> ToolResult:
        bind_context(job_id=ctx.job_id, project_id=ctx.project.id if ctx.project else None)
        tool = self._registry.get(name)
        if tool is None:
            await self._audit_rejection(ctx, name, None, "unknown tool")
            return ToolResult(tool=name, status=ToolStatus.INVALID_INPUT, error="unknown tool")
        if self._platform not in tool.spec.platforms:
            return ToolResult(
                tool=name, status=ToolStatus.UNSUPPORTED, error=f"not supported on {self._platform}"
            )
        try:
            inp = tool.spec.input_model.model_validate(dict(raw_input))
        except ValidationError as exc:
            return ToolResult(tool=name, status=ToolStatus.INVALID_INPUT, error=_truncate(str(exc)))

        try:
            plan = await tool.plan(inp, ctx)
        except PlanRejectedError as exc:
            await self._audit_rejection(ctx, name, None, exc.reason)
            return ToolResult(tool=name, status=ToolStatus.DENIED, error=exc.reason)

        if plan.action not in tool.spec.capabilities:
            reason = f"tool produced undeclared action class {plan.action!r}"
            await self._audit_rejection(ctx, name, plan, reason)
            return ToolResult(tool=name, status=ToolStatus.DENIED, action=plan.action, error=reason)

        decision = self._policy.evaluate(
            PolicyRequest(
                action=plan.action,
                actor=ctx.actor,
                in_scope=plan.in_scope,
                project_id=ctx.project.id if ctx.project else None,
                overrides=ctx.policy_overrides,
            )
        )
        if not (
            decision.decision == PolicyDecision.ALLOW and decision.level == PermissionLevel.P0_READ
        ):
            await self._audit.record(
                actor=ctx.actor,
                event_type="policy.decision",
                action=plan.action,
                project_id=ctx.project.id if ctx.project else None,
                job_id=ctx.job_id,
                policy_decision=decision.decision,
                metadata={
                    "tool": name,
                    "target": plan.target,
                    "summary": plan.summary,
                    "level": decision.level.name,
                    "reasons": list(decision.reasons),
                    "facts": plan.facts,
                },
            )

        if decision.decision == PolicyDecision.DENY:
            return ToolResult(
                tool=name,
                status=ToolStatus.DENIED,
                action=plan.action,
                decision=decision,
                error="; ".join(decision.reasons),
            )

        if decision.decision == PolicyDecision.REQUIRE_APPROVAL:
            if approval_token is None:
                return await self._request_approval(name, plan, decision, ctx)
            try:
                await self._approvals.consume(
                    approval_token,
                    action_type=plan.action,
                    target=plan.target,
                    payload=plan.payload,
                    actor=ctx.actor,
                )
            except ApprovalError as exc:
                return ToolResult(
                    tool=name,
                    status=ToolStatus.DENIED,
                    action=plan.action,
                    decision=decision,
                    error=f"approval rejected: {exc.reason}",
                )

        return await self._execute(tool, inp, plan, decision, ctx)

    # ------------------------------------------------------------------ internals
    async def _request_approval(
        self, name: str, plan: ActionPlan, decision: PolicyResult, ctx: ToolContext
    ) -> ToolResult:
        approval = await self._approvals.request(
            action_type=plan.action,
            target=plan.target,
            payload=plan.payload,
            summary=plan.summary,
            actor=ctx.actor,
            job_id=ctx.job_id,
            project_id=ctx.project.id if ctx.project else None,
        )
        if ctx.job_id is not None:
            job = await self._jobs.get(ctx.job_id)
            if job.status != JobStatus.WAITING_FOR_APPROVAL and can_transition(
                job.status, JobStatus.WAITING_FOR_APPROVAL
            ):
                await self._jobs.transition(
                    ctx.job_id,
                    JobStatus.WAITING_FOR_APPROVAL,
                    actor="system",
                    reason=f"approval {approval.id} required for {plan.action}",
                )
        return ToolResult(
            tool=name,
            status=ToolStatus.APPROVAL_REQUIRED,
            action=plan.action,
            decision=decision,
            approval_id=approval.id,
        )

    async def _execute(
        self,
        tool: Tool[Any, Any],
        inp: BaseModel,
        plan: ActionPlan,
        decision: PolicyResult,
        ctx: ToolContext,
    ) -> ToolResult:
        name = tool.spec.name
        step = await self._start_step(name, plan, ctx)
        if step is not None:
            bind_context(step_id=step.id)
        permit = ExecutionPermit._issue(name, plan)
        status, output, error = ToolStatus.SUCCEEDED, None, None
        try:
            result = await asyncio.wait_for(tool.run(inp, ctx, permit), tool.spec.timeout_s)
            if not isinstance(result, tool.spec.output_model):
                raise ToolExecutionError("tool returned an invalid output type")
            output = result.model_dump(mode="json")
        except TimeoutError:
            status, error = ToolStatus.TIMED_OUT, f"timed out after {tool.spec.timeout_s}s"
        except CancelledByUserError:
            status, error = ToolStatus.CANCELLED, "cancelled"
        except ToolExecutionError as exc:
            status, error = ToolStatus.FAILED, str(exc)
            if exc.details:
                output = redact(exc.details)
        except Exception as exc:
            log.exception("tool_crashed", tool=name)
            status, error = ToolStatus.FAILED, f"internal error: {type(exc).__name__}"

        await self._finish_step(step, status, output, error)
        if tool.spec.side_effect != SideEffect.NONE or status != ToolStatus.SUCCEEDED:
            await self._audit.record(
                actor=ctx.actor,
                event_type="tool.executed",
                action=plan.action,
                project_id=ctx.project.id if ctx.project else None,
                job_id=ctx.job_id,
                policy_decision=decision.decision,
                metadata={
                    "tool": name,
                    "status": status,
                    "summary": plan.summary,
                    "step_id": step.id if step else None,
                    "error": error,
                },
            )
        return ToolResult(
            tool=name,
            status=status,
            action=plan.action,
            output=output,
            error=error,
            decision=decision,
            step_id=step.id if step else None,
        )

    async def _start_step(self, name: str, plan: ActionPlan, ctx: ToolContext) -> JobStep | None:
        if ctx.job_id is None:
            return None
        async with self._uow_factory() as uow:
            sequence = len(await uow.jobs.list_steps(ctx.job_id)) + 1
            step = JobStep(
                job_id=ctx.job_id,
                sequence=sequence,
                agent_type=ctx.actor,
                tool_name=name,
                status=StepStatus.RUNNING,
                input_summary=_truncate(str(redact(plan.summary))),
                started_at=utc_now(),
            )
            await uow.jobs.add_step(step)
            await uow.commit()
        return step

    async def _finish_step(
        self, step: JobStep | None, status: ToolStatus, output: Any, error: str | None
    ) -> None:
        if step is None:
            return
        step.status = {
            ToolStatus.SUCCEEDED: StepStatus.SUCCEEDED,
            ToolStatus.CANCELLED: StepStatus.CANCELLED,
        }.get(status, StepStatus.FAILED)
        summary = error if error else str(redact(output)) if output is not None else ""
        step.output_summary = _truncate(summary)
        step.completed_at = utc_now()
        async with self._uow_factory() as uow:
            await uow.jobs.update_step(step)
            await uow.commit()

    async def _audit_rejection(
        self, ctx: ToolContext, name: str, plan: ActionPlan | None, reason: str
    ) -> None:
        await self._audit.record(
            actor=ctx.actor,
            event_type="tool.rejected",
            action=plan.action if plan else name,
            project_id=ctx.project.id if ctx.project else None,
            job_id=ctx.job_id,
            policy_decision=PolicyDecision.DENY,
            metadata={"tool": name, "reason": reason},
        )
