"""Controlled shell tool (FR-012, ADR-007).

* ``direct`` profile (default): ``argv`` is executed **without a shell**.
* Script profiles (``powershell``, ``pwsh``, ``bash``, ``zsh``, ``sh``, ``wsl``): the script
  is wrapped in the interpreter's argv and analysed by the same classifier, which parses
  the script and classifies every inner command.

The plan's action class is the most restrictive finding, so ``git commit`` typed into
a shell needs the same approval as a commit requested any other way.
"""

import os
from pathlib import Path
from typing import ClassVar, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from panos_core.policy import load_default_policy
from panos_core.shell.classifier import Finding, classify_argv
from panos_core.shell.environment import (
    batch_argument_problem,
    build_child_env,
    resolve_executable,
)
from panos_core.shell.process import run_process
from panos_core.tools.contract import (
    ActionPlan,
    CancelledByUserError,
    PlanRejectedError,
    SideEffect,
    Tool,
    ToolContext,
    ToolExecutionError,
    ToolSpec,
)
from panos_core.tools.paths import PathPolicyError, relative_to_root, resolve_in_root

Profile = Literal["direct", "powershell", "pwsh", "bash", "zsh", "sh", "wsl"]

MAX_TIMEOUT_S: Final = 3600.0
SHELL_CAPABILITIES: Final = frozenset(
    {
        "shell.read",
        "build.test",
        "shell.write",
        "shell.delete",
        "shell.unknown",
        "pkg.install",
        "net.fetch",
        "net.upload",
        "git.read",
        "git.mutate.local",
        "git.commit",
        "git.mutate.shared",
        "git.destructive",
        "docker.inspect",
        "docker.exec",
        "k8s.read",
        "k8s.mutate",
        "infra.mutate",
    }
)


class ShellInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    argv: list[str] = Field(default_factory=list, max_length=256)
    profile: Profile = "direct"
    script: str | None = Field(default=None, max_length=100_000)
    cwd: str = "."
    timeout_s: float = Field(default=600.0, gt=0, le=MAX_TIMEOUT_S)
    env: dict[str, str] = Field(default_factory=dict)
    max_output_bytes: int = Field(default=1_000_000, ge=1024, le=10_000_000)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.profile == "direct":
            if not self.argv or self.script is not None:
                raise ValueError("direct profile requires argv and no script")
            if any("\x00" in a or len(a) > 32_768 for a in self.argv):
                raise ValueError("invalid argument (NUL byte or too long)")
        elif not self.script or self.argv:
            raise ValueError("script profiles require script and no argv")
        return self


class ShellOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_s: float
    timed_out: bool


def wrap_profile(inp: ShellInput) -> list[str]:
    if inp.profile == "direct":
        return list(inp.argv)
    script = inp.script or ""
    if inp.profile in ("powershell", "pwsh"):
        return [inp.profile, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script]
    if inp.profile == "wsl":
        return ["wsl", "-e", "bash", "-c", script]
    return [inp.profile, "-c", script]


def most_restrictive(findings: list[Finding]) -> Finding:
    policy = load_default_policy()

    def rank(f: Finding) -> int:
        level = policy.level_of(f.action)
        return int(level) if level is not None else 99

    return max(findings, key=rank)


class ShellTool(Tool[ShellInput, ShellOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="shell.run",
        version="1",
        description="Run a classified command in the project (no shell unless a script "
        "profile is chosen).",
        input_model=ShellInput,
        output_model=ShellOutput,
        capabilities=SHELL_CAPABILITIES,
        side_effect=SideEffect.EXTERNAL,
        timeout_s=MAX_TIMEOUT_S + 30,
        cancellable=True,
        idempotent=False,
        privacy="Runs commands with project files; output may contain project content.",
    )

    def _locate(self, inp: ShellInput, ctx: ToolContext) -> tuple[Path, Path, list[str], Path]:
        if ctx.project is None:
            raise PlanRejectedError("no active project")
        root = Path(ctx.project.path).resolve()
        try:
            cwd = resolve_in_root(root, inp.cwd)
        except PathPolicyError as exc:
            raise PlanRejectedError(f"cwd: {exc}") from exc
        if not cwd.is_dir():
            raise PlanRejectedError("cwd is not a directory")
        argv = wrap_profile(inp)
        exe = resolve_executable(argv[0], os.environ.get("PATH", ""), cwd=cwd, root=root)
        if exe is None:
            raise PlanRejectedError(f"executable not found or outside the project: {argv[0]!r}")
        problem = batch_argument_problem(exe, argv[1:])
        if problem:
            raise PlanRejectedError(problem)
        return root, cwd, argv, exe

    async def plan(self, inp: ShellInput, ctx: ToolContext) -> ActionPlan:
        root, cwd, argv, exe = self._locate(inp, ctx)
        try:
            build_child_env({}, inp.env)  # validate override names early
        except ValueError as exc:
            raise PlanRejectedError(str(exc)) from exc
        findings = classify_argv(argv, allowed=ctx.allowed_commands)
        worst = most_restrictive(findings)
        rel_cwd = relative_to_root(root, cwd)
        display = " ".join(argv) if inp.profile == "direct" else f"[{inp.profile}] {inp.script}"
        return ActionPlan(
            action=worst.action,
            target=str(root),
            summary=f"Run in {rel_cwd}: {display[:300]}",
            payload={
                "argv": argv,
                "executable": str(exe),
                "cwd": rel_cwd,
                "env": inp.env,
                "profile": inp.profile,
            },
            facts={
                "findings": [
                    {"action": f.action, "reason": f.reason, "command": list(f.command)[:20]}
                    for f in findings
                ]
            },
        )

    async def _execute(self, inp: ShellInput, ctx: ToolContext, plan: ActionPlan) -> ShellOutput:
        _root, cwd, argv, exe = self._locate(inp, ctx)
        if str(exe) != plan.payload["executable"]:
            raise ToolExecutionError("executable resolution changed after planning")
        env, _removed = build_child_env(os.environ, inp.env)
        result = await run_process(
            [str(exe), *argv[1:]],
            cwd=cwd,
            env=env,
            timeout_s=inp.timeout_s,
            cancel=ctx.cancel,
            max_output_bytes=inp.max_output_bytes,
        )
        if result.cancelled:
            raise CancelledByUserError("cancelled")
        if result.timed_out:
            raise TimeoutError(f"command timed out after {inp.timeout_s}s")
        return ShellOutput(
            command=argv,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
            duration_s=result.duration_s,
            timed_out=False,
        )
