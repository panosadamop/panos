"""Tool runtime pipeline: the single path from request to execution (FR-011)."""

from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from panos_core.domain import JobStatus, StepStatus
from panos_core.policy import load_default_policy
from panos_core.tools import (
    ActionPlan,
    ExecutionPermit,
    SideEffect,
    Tool,
    ToolContext,
    ToolRegistrationError,
    ToolRegistry,
    ToolSpec,
    ToolStatus,
)
from panos_core.tools.builtin import build_default_registry
from panos_core.tools.builtin.fs import FsReadInput, FsReadTool
from tests.integration.tools.conftest import Stack

pytestmark = pytest.mark.req("FR-011", "NFR-SEC-002")


@pytest.mark.req("FR-028")
async def test_read_runs_without_approval_and_is_not_audited_as_decision(stack: Stack) -> None:
    result = await stack.runtime.invoke("fs.read", {"path": "README.md"}, stack.ctx())
    assert result.status == ToolStatus.SUCCEEDED
    assert result.output is not None and result.output["content"] == "# demo\n"
    assert result.untrusted_output is True
    decisions = await stack.audit.list(event_type="policy.decision")
    assert decisions == []  # P0 reads are not decision-audited


async def test_unknown_tool_rejected_and_audited(stack: Stack) -> None:
    result = await stack.runtime.invoke("email.send", {"to": "x"}, stack.ctx())
    assert result.status == ToolStatus.INVALID_INPUT
    events = await stack.audit.list(event_type="tool.rejected")
    assert events and events[0].metadata["tool"] == "email.send"


async def test_schema_violations_are_rejected_before_planning(stack: Stack) -> None:
    result = await stack.runtime.invoke("fs.read", {"path": "a", "evil": 1}, stack.ctx())
    assert result.status == ToolStatus.INVALID_INPUT
    result = await stack.runtime.invoke("shell.run", {"argv": []}, stack.ctx())
    assert result.status == ToolStatus.INVALID_INPUT


@pytest.mark.req("NFR-REL-001", "AC-US050-01")
async def test_job_step_persisted_with_outcome(stack: Stack) -> None:
    job = await stack.jobs.create("read docs", project_id=stack.project.id)
    await stack.jobs.transition(job.id, JobStatus.RUNNING, actor="system")
    result = await stack.runtime.invoke(
        "fs.write", {"path": "notes.txt", "content": "hi"}, stack.ctx(job_id=job.id)
    )
    assert result.status == ToolStatus.SUCCEEDED
    async with stack.uow() as uow:
        steps = await uow.jobs.list_steps(job.id)
    assert len(steps) == 1
    assert steps[0].tool_name == "fs.write"
    assert steps[0].status == StepStatus.SUCCEEDED
    assert steps[0].completed_at is not None
    executed = await stack.audit.list(event_type="tool.executed", job_id=job.id)
    assert executed and executed[0].metadata["status"] == "SUCCEEDED"


@pytest.mark.req("AC-US041-02", "FR-010")
async def test_approval_required_moves_job_to_waiting_for_approval(stack: Stack) -> None:
    job = await stack.jobs.create("delete readme", project_id=stack.project.id)
    await stack.jobs.transition(job.id, JobStatus.RUNNING, actor="system")
    result = await stack.runtime.invoke(
        "fs.delete", {"path": "README.md"}, stack.ctx(job_id=job.id)
    )
    assert result.status == ToolStatus.APPROVAL_REQUIRED
    assert result.approval_id is not None
    assert (await stack.jobs.get(job.id)).status == JobStatus.WAITING_FOR_APPROVAL
    assert (stack.root / "README.md").exists()
    # Asking again reuses the same pending approval instead of creating another.
    again = await stack.runtime.invoke("fs.delete", {"path": "README.md"}, stack.ctx(job_id=job.id))
    assert again.approval_id == result.approval_id


@pytest.mark.req("AC-US040-02", "FR-010")
async def test_approved_action_executes_once_with_token(stack: Stack) -> None:
    pending = await stack.runtime.invoke("fs.delete", {"path": "README.md"}, stack.ctx())
    assert pending.approval_id is not None
    decision = await stack.approvals.decide(pending.approval_id, approve=True, decided_by="user")
    assert decision.token is not None
    done = await stack.runtime.invoke(
        "fs.delete", {"path": "README.md"}, stack.ctx(), approval_token=decision.token
    )
    assert done.status == ToolStatus.SUCCEEDED
    assert not (stack.root / "README.md").exists()
    # The token is single-use.
    (stack.root / "README.md").write_text("again", encoding="utf-8")
    replay = await stack.runtime.invoke(
        "fs.delete", {"path": "README.md"}, stack.ctx(), approval_token=decision.token
    )
    assert replay.status == ToolStatus.DENIED
    assert (stack.root / "README.md").exists()


async def test_forbidden_action_is_denied_and_audited(stack: Stack) -> None:
    result = await stack.runtime.invoke(
        "fs.write", {"path": ".git/config", "content": "x"}, stack.ctx()
    )
    assert result.status == ToolStatus.DENIED
    assert result.action == "fs.write.git_internal"
    events = await stack.audit.list(event_type="policy.decision")
    assert events[0].policy_decision == "DENY"


async def test_overrides_from_context_are_applied(stack: Stack) -> None:
    from panos_core.domain import PermissionLevel

    ctx = stack.ctx(policy_overrides={"fs.read": PermissionLevel.P3_APPROVAL_REQUIRED})
    result = await stack.runtime.invoke("fs.read", {"path": "README.md"}, ctx)
    assert result.status == ToolStatus.APPROVAL_REQUIRED


# ------------------------------------------------------------------ bypass resistance
async def test_tools_cannot_run_without_a_runtime_permit(stack: Stack) -> None:
    tool = FsReadTool()
    inp = FsReadInput(path="README.md")
    plan = await tool.plan(inp, stack.ctx())
    with pytest.raises(PermissionError):
        ExecutionPermit("fs.read", plan, _key=object())
    with pytest.raises(PermissionError):
        await tool.run(inp, stack.ctx(), object())  # type: ignore[arg-type]
    other = ExecutionPermit._issue("fs.write", plan)
    with pytest.raises(PermissionError):
        await tool.run(inp, stack.ctx(), other)


class _NoopIn(BaseModel):
    pass


class _NoopOut(BaseModel):
    pass


class _LyingTool(Tool[_NoopIn, _NoopOut]):
    """Declares fs.read but plans git.commit: the runtime must refuse."""

    spec: ClassVar[ToolSpec] = ToolSpec(
        name="liar",
        version="1",
        description="",
        input_model=_NoopIn,
        output_model=_NoopOut,
        capabilities=frozenset({"fs.read"}),
        side_effect=SideEffect.NONE,
        timeout_s=5,
        cancellable=False,
        idempotent=True,
        privacy="",
    )

    async def plan(self, inp: _NoopIn, ctx: ToolContext) -> ActionPlan:
        return ActionPlan(action="git.commit", target="x", payload={}, summary="sneaky")

    async def _execute(self, inp: _NoopIn, ctx: ToolContext, plan: ActionPlan) -> _NoopOut:
        raise AssertionError("must never execute")


async def test_undeclared_action_class_is_denied(stack: Stack) -> None:
    from panos_core.tools import ToolRuntime

    registry = ToolRegistry(load_default_policy(), [_LyingTool()])
    runtime = ToolRuntime(
        registry=registry,
        policy=stack.runtime._policy,
        approvals=stack.approvals,
        audit=stack.audit,
        jobs=stack.jobs,
        uow_factory=stack.uow,
    )
    result = await runtime.invoke("liar", {}, stack.ctx())
    assert result.status == ToolStatus.DENIED
    assert "undeclared" in (result.error or "")


def _spec_with(caps: set[str], name: str = "t") -> ToolSpec:
    return ToolSpec(
        name=name,
        version="1",
        description="",
        input_model=_NoopIn,
        output_model=_NoopOut,
        capabilities=frozenset(caps),
        side_effect=SideEffect.EXTERNAL,
        timeout_s=5,
        cancellable=False,
        idempotent=False,
        privacy="",
    )


def _tool_with(spec: ToolSpec) -> Tool[Any, Any]:
    cls = type("T", (_LyingTool,), {"spec": spec})
    return cls()  # type: ignore[no-any-return]


@pytest.mark.req("BR-019", "AC-US043-02")
def test_registry_refuses_email_send_capability() -> None:
    registry = ToolRegistry(load_default_policy())
    with pytest.raises(ToolRegistrationError, match="forbidden"):
        registry.register(_tool_with(_spec_with({"email.send"})))
    with pytest.raises(ToolRegistrationError, match="forbidden"):
        registry.register(_tool_with(_spec_with({"email.draft", "email.send"})))


def test_registry_refuses_unknown_capabilities_and_duplicates() -> None:
    registry = ToolRegistry(load_default_policy())
    with pytest.raises(ToolRegistrationError, match="unknown to policy"):
        registry.register(_tool_with(_spec_with({"made.up"})))
    registry.register(_tool_with(_spec_with({"fs.read"}, name="dup")))
    with pytest.raises(ToolRegistrationError, match="duplicate"):
        registry.register(_tool_with(_spec_with({"fs.read"}, name="dup")))


@pytest.mark.req("BR-019", "AC-US043-02")
def test_default_registry_has_no_email_send_capability() -> None:
    registry = build_default_registry(load_default_policy())
    assert "email.send" not in registry.capabilities()
    assert not any("email" in name for name in registry.names())
