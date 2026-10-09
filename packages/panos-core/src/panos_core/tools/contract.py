"""Tool contract (TOOLS.md).

A tool is split into two phases:

* ``plan`` — pure analysis of the validated input: resolves the target, determines the
  *action class* that policy evaluates (e.g. a shell command classified as
  ``git.commit``) and the exact payload an approval binds to. No side effects.
* ``run`` — executes the plan. It requires an :class:`ExecutionPermit`, which only the
  :class:`~panos_core.tools.runtime.ToolRuntime` creates after schema validation,
  policy evaluation and (if required) approval consumption. Tools therefore cannot be
  executed by bypassing the runtime.
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict

from panos_core.domain.enums import PermissionLevel
from panos_core.domain.models import Project

ALL_PLATFORMS: Final = frozenset({"windows", "macos", "linux"})


class SideEffect(StrEnum):
    NONE = "none"  # read-only
    LOCAL = "local"  # modifies local files/processes
    EXTERNAL = "external"  # affects systems outside this machine


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    capabilities: frozenset[str]  # action classes this tool may produce
    side_effect: SideEffect
    timeout_s: float
    cancellable: bool
    idempotent: bool
    privacy: str  # privacy implications, shown in docs/UI
    platforms: frozenset[str] = ALL_PLATFORMS


class ActionPlan(BaseModel):
    """What a tool invocation will do, as evaluated by policy and bound by approvals."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str
    target: str
    payload: dict[str, Any]
    summary: str
    in_scope: bool = True
    facts: dict[str, Any] = {}  # classification evidence, for audit/UI only


class CancelledByUserError(Exception):
    """Raised inside tools when the invocation's cancel token fires."""


class CancelToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise CancelledByUserError("cancelled")


@dataclass(frozen=True)
class ToolContext:
    actor: str
    node_id: str
    project: Project | None = None
    job_id: str | None = None
    cancel: CancelToken = field(default_factory=CancelToken)
    policy_overrides: dict[str, PermissionLevel] = field(default_factory=dict)
    # Project-specific allowlisted build/test commands (discovered in M3), as argv prefixes.
    allowed_commands: tuple[tuple[str, ...], ...] = ()


class PlanRejectedError(Exception):
    """The input is unsafe or invalid for this tool (e.g. a path escaping the project)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ToolExecutionError(Exception):
    """A tool failed while executing; ``details`` is safe to show and log."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


_PERMIT_KEY: Final = object()


class ExecutionPermit:
    """Proof that the runtime authorized exactly this plan for this tool."""

    __slots__ = ("plan", "tool_name")

    def __init__(self, tool_name: str, plan: ActionPlan, *, _key: object) -> None:
        if _key is not _PERMIT_KEY:
            raise PermissionError("execution permits are issued only by the ToolRuntime")
        self.tool_name = tool_name
        self.plan = plan

    @classmethod
    def _issue(cls, tool_name: str, plan: ActionPlan) -> "ExecutionPermit":
        return cls(tool_name, plan, _key=_PERMIT_KEY)


class Tool[I: BaseModel, O: BaseModel](ABC):
    spec: ClassVar[ToolSpec]

    @abstractmethod
    async def plan(self, inp: I, ctx: ToolContext) -> ActionPlan:
        """Analyse the input without side effects. Raise PlanRejectedError if unsafe."""

    @abstractmethod
    async def _execute(self, inp: I, ctx: ToolContext, plan: ActionPlan) -> O:
        """Perform the planned action."""

    async def run(self, inp: I, ctx: ToolContext, permit: ExecutionPermit) -> O:
        if not isinstance(permit, ExecutionPermit) or permit.tool_name != self.spec.name:
            raise PermissionError(f"invalid execution permit for {self.spec.name}")
        return await self._execute(inp, ctx, permit.plan)
