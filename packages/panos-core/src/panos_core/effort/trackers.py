"""Import tracker worklogs as *pending* actuals (M10-03, ADR-031 §2).

Only work items linked to a requirement as ``workitem:<system>:<key>`` evidence (M8-06) are
read. Jira worklogs map one-to-one (external ref ``jira:<key>:<worklog id>``). Azure DevOps
only keeps a cumulative "completed work", so the increase since the last import becomes a new
entry (ref ``ado:<id>:<total>``). Closed items mark their last entry as finishing the story.
Nothing counts for calibration until the owner approves it.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from panos_core.connectors.http import ConnectorHttpError
from panos_core.connectors.service import ConnectorError, ConnectorService
from panos_core.connectors.workitems import AzureDevOpsClient, JiraClient
from panos_core.domain import Project
from panos_core.domain.connectors import ConnectorKind
from panos_core.domain.effort import ActualStatus, EffortActual, EffortSource
from panos_core.effort.service import EffortService
from panos_core.requirements import RequirementsService

DONE: Final = frozenset({"done", "closed", "resolved", "completed"})


@dataclass
class ImportResult:
    imported: int = 0
    skipped: int = 0  # already imported
    items: int = 0
    errors: list[str] = field(default_factory=list)


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return date.today()  # noqa: DTZ011


async def import_worklogs(
    project: Project,
    *,
    connectors: ConnectorService,
    requirements: RequirementsService,
    effort: EffortService,
    actor: str = "user",
) -> ImportResult:
    result = ImportResult()
    linked: dict[tuple[str, str], set[str]] = defaultdict(set)  # (system, key) → requirement keys
    for link in await requirements.links(project.id):
        if link.target.startswith("workitem:"):
            _, system, key = link.target.split(":", 2)
            linked[(system, key)].add(link.source_key)
    if not linked:
        return result
    existing = [a for a in await effort.actuals(project, source=EffortSource.TRACKER.value)]
    for (system, key), req_keys in sorted(linked.items()):
        result.items += 1
        requirement = sorted(req_keys)[0]
        try:
            account = await connectors.first(ConnectorKind(system))
            http = connectors.client(account)
        except (ConnectorError, ValueError) as exc:
            result.errors.append(f"{system}:{key}: {exc}")
            continue
        try:
            if system == ConnectorKind.JIRA.value:
                client = JiraClient(http, account.base_url or "")
                status = (await client.get(key)).status.casefold()
                logs = await client.worklogs(key)
                for i, w in enumerate(logs):
                    actual = EffortActual(
                        project_id=project.id,
                        requirement_key=requirement,
                        source=EffortSource.TRACKER,
                        hours=w["hours"],
                        work_date=_day(w["started"]),
                        external_ref=f"jira:{key}:{w['id']}",
                        note=f"Jira {key} worklog by {w['author']}"[:500],
                        completes=status in DONE and i == len(logs) - 1,
                        status=ActualStatus.PENDING,
                        logged_by=f"tracker:{account.label}",
                    )
                    if await effort.add_imported(actual, actor=actor):
                        result.imported += 1
                    else:
                        result.skipped += 1
            else:
                ado = AzureDevOpsClient(http, account.base_url or "")
                total = round(await ado.completed_work(key), 2)
                item = await ado.get(key)
                prefix = f"ado:{key}:"
                already = round(
                    sum(
                        a.hours
                        for a in existing
                        if (a.external_ref or "").startswith(prefix)
                        and a.status != ActualStatus.REJECTED
                    ),
                    2,
                )
                if total > already:
                    actual = EffortActual(
                        project_id=project.id,
                        requirement_key=requirement,
                        source=EffortSource.TRACKER,
                        hours=round(total - already, 2),
                        work_date=date.today(),  # noqa: DTZ011
                        external_ref=f"{prefix}{total}",
                        note=f"Azure DevOps #{key} completed work now {total} h",
                        completes=item.status.casefold() in DONE,
                        status=ActualStatus.PENDING,
                        logged_by=f"tracker:{account.label}",
                    )
                    if await effort.add_imported(actual, actor=actor):
                        result.imported += 1
                    else:
                        result.skipped += 1
                else:
                    result.skipped += 1
        except (ConnectorHttpError, ValueError) as exc:
            result.errors.append(f"{system}:{key}: {exc}")
        finally:
            await http.aclose()
    return result
