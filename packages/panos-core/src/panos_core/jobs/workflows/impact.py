"""Impact-analysis task (ADR-014, REQUIREMENTS_PIPELINE "Change control", AC-US032-03).

Created when an approved requirement changes. It lists the downstream items now marked
stale and waits for the user (WAITING_FOR_USER) until every one of them has been
re-approved or explicitly cleared; resuming re-checks and completes when none remain.
"""

from typing import Any

from panos_core.jobs.context import Blocked, JobContext, NeedsUser


class ImpactAnalysisWorkflow:
    kind = "impact_analysis"

    async def run(self, ctx: JobContext) -> dict[str, Any]:
        service = ctx.services.requirements
        if service is None or ctx.project is None:
            raise Blocked("impact analysis needs the requirements service and a project")
        changed = str(ctx.job.input.get("changed_key", ""))
        keys = [str(k) for k in ctx.job.input.get("stale_keys", [])]
        async with ctx.step("orchestrator", f"Impact of changing {changed}") as step:
            items = {i.key: i for i in await service.list_latest(ctx.project.id)}
            still = [k for k in keys if k in items and items[k].stale]
            step.output = f"{len(still)} of {len(keys)} item(s) still stale"
        report = {
            "changed_key": changed,
            "affected": [
                {
                    "key": k,
                    "title": items[k].title,
                    "status": items[k].status.value,
                    "stale": items[k].stale,
                }
                for k in keys
                if k in items
            ],
        }
        if still:
            raise NeedsUser(
                f"review {len(still)} stale item(s) affected by {changed}: "
                + ", ".join(still[:10]),
                report,
            )
        return {"success": True, "summary": f"all items affected by {changed} reviewed", **report}
