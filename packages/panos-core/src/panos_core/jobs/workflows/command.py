"""Command job: run one command in the project as a background job (e.g. the test suite).

The command goes through ``shell.run`` and is classified like any other command, so
discovered build/test commands run directly (P1) and anything riskier waits for approval
(``docker compose up`` → ``docker.exec`` P3, US-041). Cancel kills its process tree.
"""

from typing import Any

from panos_core.jobs.context import Blocked, JobContext
from panos_core.tools.runtime import ToolStatus

_TAIL = 4000


class CommandWorkflow:
    kind = "command"

    async def run(self, ctx: JobContext) -> dict[str, Any]:
        argv = [str(a) for a in ctx.job.input.get("argv", [])]
        if not argv:
            raise Blocked("command job without argv")
        await ctx.safe_point()
        payload = {"argv": argv, "timeout_s": float(ctx.job.input.get("timeout_s", 1800))}
        async with ctx.step("agent:qa", f"Run {' '.join(argv)[:200]}", tool="shell.run") as step:
            result = await ctx.tool("shell.run", payload, actor="agent:qa")
            step.output = result.status.value
        out = result.output or {}
        exit_code = out.get("exit_code")
        success = result.status == ToolStatus.SUCCEEDED and exit_code == 0
        return {
            "success": success,
            "reason": "" if success else (result.error or f"exit code {exit_code}"),
            "argv": argv,
            "status": result.status.value,
            "exit_code": exit_code,
            "stdout": str(out.get("stdout", ""))[-_TAIL:],
            "stderr": str(out.get("stderr", ""))[-_TAIL:],
        }
