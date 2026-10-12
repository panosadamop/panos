"""Tracker worklogs become pending actuals (M10-03, ADR-031 §2, BR-015, BR-016)."""

from pathlib import Path
from typing import Any

import httpx
import pytest

from panos_core.config import PanosSettings
from panos_core.config.settings import EstimationSettings
from panos_core.domain.effort import ActualStatus
from panos_core.domain.requirements import RequirementType as T
from panos_core.domain.requirements import TraceRelation
from panos_core.effort.service import EffortService
from panos_core.effort.trackers import import_worklogs
from panos_core.projects import ProjectService
from panos_core.requirements import NewItem
from tests.connector_fakes import query
from tests.integration.connectors.conftest import Env

pytestmark = pytest.mark.req("BR-015", "BR-016")

SITE = "https://acme.atlassian.net"
ORG = "https://dev.azure.com/acme"


async def test_jira_and_ado_imports_are_pending_and_deduplicated(
    env: Env, settings: PanosSettings, tmp_path: Path
) -> None:
    (tmp_path / "shop").mkdir()
    project = await ProjectService(env.uow_factory, env.audit, settings, env.node.id).register(
        tmp_path / "shop"
    )
    await env.requirements.create_many(
        project.id, [NewItem(T.US, "Checkout"), NewItem(T.US, "Invoices")]
    )
    for key, target in (("US-001", "workitem:jira:SHOP-1"), ("US-002", "workitem:azure_devops:42")):
        await env.requirements.link(project.id, key, target, TraceRelation.IMPLEMENTS, actor="t")

    worklogs: list[dict[str, Any]] = [
        {
            "id": 100,
            "timeSpentSeconds": 5400,
            "started": "2026-10-05T09:00:00.000+0300",
            "author": {"displayName": "Maria"},
        },
        {
            "id": 101,
            "timeSpentSeconds": 7200,
            "started": "2026-10-06T09:00:00.000+0300",
            "author": {"displayName": "Maria"},
        },
    ]
    completed = {"value": 3.0}

    def jira(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/worklog"):
            return httpx.Response(200, json={"worklogs": worklogs})
        return httpx.Response(
            200,
            json={"key": "SHOP-1", "fields": {"summary": "Checkout", "status": {"name": "Done"}}},
        )

    def ado(request: httpx.Request) -> httpx.Response:
        fields = query(request).get("fields", "")
        if "CompletedWork" in fields:
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "fields": {"Microsoft.VSTS.Scheduling.CompletedWork": completed["value"]},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": 42,
                "fields": {
                    "System.Title": "Invoices",
                    "System.State": "Active",
                    "System.TeamProject": "Shop",
                },
            },
        )

    env.cloud.jira_handler = jira  # type: ignore[attr-defined]
    env.cloud.ado_handler = ado  # type: ignore[attr-defined]
    await env.service.connect_jira(SITE, "owner@example.com", "jira-tok")
    await env.service.connect_azure_devops(ORG, "ado-pat")
    effort = EffortService(env.uow_factory, env.audit, EstimationSettings())

    async def run() -> Any:
        return await import_worklogs(
            project, connectors=env.service, requirements=env.requirements, effort=effort
        )

    first = await run()
    assert (first.imported, first.items, first.errors) == (3, 2, [])
    actuals = {a.external_ref: a for a in await effort.actuals(project)}
    assert set(actuals) == {"jira:SHOP-1:100", "jira:SHOP-1:101", "ado:42:3.0"}
    assert all(a.status == ActualStatus.PENDING for a in actuals.values())
    assert actuals["jira:SHOP-1:100"].hours == 1.5 and not actuals["jira:SHOP-1:100"].completes
    assert actuals["jira:SHOP-1:101"].completes  # the issue is Done: its last entry finishes it
    assert actuals["ado:42:3.0"].requirement_key == "US-002" and not actuals["ado:42:3.0"].completes

    again = await run()
    assert again.imported == 0 and again.skipped == 3  # deduplicated by external ref
    completed["value"] = 5.5  # two and a half more hours on the ADO item
    third = await run()
    assert third.imported == 1
    delta = {a.external_ref: a for a in await effort.actuals(project)}["ado:42:5.5"]
    assert delta.hours == 2.5
    assert not any(r.method != "GET" for r in env.cloud.requests if "worklog" in str(r.url))
