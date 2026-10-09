"""Requirements: keys, versions, hierarchical approval, change control, traceability."""

from pathlib import Path

import pytest

from panos_core.audit import AuditService
from panos_core.config import PanosSettings
from panos_core.domain.requirements import RequirementStatus as S
from panos_core.domain.requirements import RequirementType as T
from panos_core.domain.requirements import TraceRelation as R
from panos_core.nodes import ensure_local_node
from panos_core.persistence import UnitOfWorkFactory
from panos_core.projects import ProjectService
from panos_core.requirements import NewItem, RequirementsError, RequirementsService

pytestmark = pytest.mark.req("FR-008", "FR-010", "BR-009", "BR-010")


@pytest.fixture
async def svc(
    uow_factory: UnitOfWorkFactory, settings: PanosSettings, tmp_path: Path
) -> tuple[RequirementsService, str]:
    node = await ensure_local_node(uow_factory)
    audit = AuditService(uow_factory, node.id)
    (tmp_path / "shop").mkdir()
    project = await ProjectService(uow_factory, audit, settings, node.id).register(
        tmp_path / "shop"
    )
    return RequirementsService(uow_factory, audit), project.id


async def tree(svc: RequirementsService, pid: str) -> dict[str, str]:
    """BR-001 → FR-001 → US-001 → AC-US001-01/02 → TC-001; plus FR-002 → US-002."""
    items = await svc.create_many(
        pid,
        [
            NewItem(T.BR, "Sell products online", ref="br"),
            NewItem(T.FR, "Customers can check out", parent_ref="br", ref="fr1"),
            NewItem(T.FR, "Admins manage the catalog", parent_ref="br", ref="fr2"),
            NewItem(
                T.US,
                "As a customer I can pay by card",
                parent_ref="fr1",
                ref="us1",
                attributes={"points": 5},
            ),
            NewItem(T.US, "As an admin I can add a product", parent_ref="fr2", ref="us2"),
            NewItem(T.AC, "Valid card is charged", parent_ref="us1", ref="ac1"),
            NewItem(T.AC, "Declined card shows an error", parent_ref="us1"),
            NewItem(T.TC, "Charge succeeds with test card", parent_ref="ac1"),
        ],
    )
    return {i.title.split()[0] + str(n): i.key for n, i in enumerate(items)}


async def test_keys_are_allocated_per_type_and_story(svc: tuple[RequirementsService, str]) -> None:
    s, pid = svc
    await tree(s, pid)
    keys = [i.key for i in await s.list_latest(pid)]
    assert keys == [
        "AC-US001-01",
        "AC-US001-02",
        "BR-001",
        "FR-001",
        "FR-002",
        "TC-001",
        "US-001",
        "US-002",
    ]
    nfr = await s.create(pid, T.NFR, "p95 checkout under 2 s", attributes={"category": "PERF"})
    assert nfr.key == "NFR-001" and nfr.status == S.DRAFT and nfr.version == 1
    assert (await s.create(pid, T.AC, "x", parent_key="US-002")).key == "AC-US002-01"
    with pytest.raises(RequirementsError):
        await s.create(pid, T.AC, "orphan criterion")
    with pytest.raises(RequirementsError):
        await s.create(pid, T.FR, "x", parent_key="BR-999")


@pytest.mark.req("US-031", "AC-US031-01", "AC-US031-02", "AC-US031-03")
async def test_cascade_with_exclusions_records_each_decision(
    svc: tuple[RequirementsService, str],
) -> None:
    s, pid = svc
    await tree(s, pid)
    records = await s.approve(pid, "BR-001", actor="pm", comments="v1 scope", exclude=["FR-002"])
    approved = {r.key for r in records}
    assert approved == {"BR-001", "FR-001", "US-001", "AC-US001-01", "AC-US001-02", "TC-001"}
    assert "FR-002" not in approved and "US-002" not in approved  # excluded subtree
    root = records[0]
    assert root.key == "BR-001" and root.cascaded_from_id is None
    assert root.excluded_keys == ["FR-002"]
    assert all(r.cascaded_from_id == root.id for r in records[1:])
    assert all(r.decided_by == "pm" and r.version == 1 and r.decided_at for r in records)
    assert (await s.get(pid, "US-002")).status == S.DRAFT
    assert (await s.get(pid, "TC-001")).status == S.APPROVED
    single = await s.approve(pid, "FR-002", actor="pm", cascade=False)
    assert [r.key for r in single] == ["FR-002"]
    assert (await s.get(pid, "US-002")).status == S.DRAFT
    with pytest.raises(RequirementsError):
        await s.approve(pid, "BR-001", actor="pm", exclude=["NOPE-001"])


@pytest.mark.req("AC-US031-04", "US-032", "AC-US032-03", "FR-009")
async def test_editing_an_approved_item_versions_it_and_marks_downstream_stale(
    svc: tuple[RequirementsService, str],
) -> None:
    s, pid = svc
    await tree(s, pid)
    await s.create(pid, T.API, "POST /payments")
    await s.link(pid, "API-001", "FR-001", R.REALIZES, actor="arch")
    await s.approve(pid, "BR-001", actor="pm")
    await s.approve(pid, "API-001", actor="pm")
    changes: list[tuple[str, list[str]]] = []

    async def hook(project: str, key: str, stale: list[str]) -> None:
        changes.append((key, stale))

    s.set_change_hook(hook)
    new, stale = await s.edit(pid, "FR-001", actor="pm", body="Also support PayPal")
    assert new.version == 2 and new.status == S.DRAFT
    assert set(stale) == {"US-001", "AC-US001-01", "AC-US001-02", "TC-001", "API-001"}
    assert changes == [("FR-001", stale)]
    assert (await s.get(pid, "US-001")).stale and not (await s.get(pid, "FR-002")).stale
    history = await s.history(pid, "FR-001")
    assert [(v.version, v.status) for v in history] == [(1, S.APPROVED), (2, S.DRAFT)]
    assert await s.check_ready(pid, ["FR-001", "US-001", "FR-002", "XX-404"]) == [
        "FR-001 is DRAFT (v2)",
        "US-001 is stale (an upstream item changed)",
    ]

    # Re-approval supersedes v1 and clears stale on the cascade.
    await s.approve(pid, "FR-001", actor="pm")
    history = await s.history(pid, "FR-001")
    assert [(v.version, v.status) for v in history] == [(1, S.SUPERSEDED), (2, S.APPROVED)]
    assert not (await s.get(pid, "US-001")).stale
    assert (await s.get(pid, "API-001")).stale  # linked, not a descendant: needs review
    assert await s.clear_stale(pid, ["API-001"], actor="arch", reason="contract unchanged") == [
        "API-001"
    ]
    assert await s.check_ready(pid, ["FR-001", "US-001", "API-001"]) == []
    approvals = await s.approvals(pid, "FR-001")
    assert [(a.version, a.decision) for a in approvals] == [(1, S.APPROVED), (2, S.APPROVED)]


async def test_editing_a_draft_replaces_it_without_change_control(
    svc: tuple[RequirementsService, str],
) -> None:
    s, pid = svc
    await tree(s, pid)
    _, stale = await s.edit(pid, "FR-002", actor="ba", title="Admins manage catalog & stock")
    assert stale == []
    assert [v.status for v in await s.history(pid, "FR-002")] == [S.SUPERSEDED, S.DRAFT]
    await s.submit(pid, "FR-002", actor="ba")
    assert (await s.get(pid, "FR-002")).status == S.PENDING_APPROVAL
    with pytest.raises(RequirementsError):
        await s.submit(pid, "FR-002", actor="ba")
    rejected = await s.reject(pid, "FR-002", actor="pm", comments="out of scope")
    assert rejected.decision == S.REJECTED and rejected.comments == "out of scope"


@pytest.mark.req("AC-US032-01", "AC-US032-02")
async def test_traceability_tree_links_evidence_and_gaps(
    svc: tuple[RequirementsService, str],
) -> None:
    s, pid = svc
    await tree(s, pid)
    await s.create(pid, T.API, "POST /payments")
    await s.create(pid, T.DATA, "Payment")
    await s.link(pid, "API-001", "FR-001", R.REALIZES, actor="arch")
    await s.link(pid, "DATA-001", "FR-001", R.REALIZES, actor="arch")
    await s.link(pid, "US-001", "commit:abc1234", R.IMPLEMENTS, actor="dev")
    again = await s.link(pid, "US-001", "commit:abc1234", R.IMPLEMENTS, actor="dev")
    assert len([x for x in await s.links(pid) if x.id == again.id]) == 1  # idempotent
    with pytest.raises(RequirementsError):
        await s.link(pid, "US-001", "not a key", R.RELATES, actor="dev")

    trace = await s.traceability(pid)
    [br] = trace["roots"]
    fr1 = next(c for c in br["children"] if c["key"] == "FR-001")
    assert {(x["relation"], x["key"]) for x in fr1["links"]} == {
        ("realizes", "API-001"),
        ("realizes", "DATA-001"),
    }
    us1 = fr1["children"][0]
    assert us1["key"] == "US-001" and us1["evidence"] == ["commit:abc1234"]
    assert [c["key"] for c in us1["children"]] == ["AC-US001-01", "AC-US001-02"]
    gaps = {g["key"]: g["missing"] for g in trace["gaps"]}
    assert gaps == {"AC-US001-02": "TC", "US-002": "AC"}  # untested criterion, story w/o ACs
    one = await s.traceability(pid, "FR-002")
    assert one["roots"][0]["key"] == "FR-002" and one["gaps"] == [
        {"key": "US-002", "missing": "AC"}
    ]
    assert await s.unlink(pid, "US-001", "commit:abc1234", R.IMPLEMENTS)
    assert trace["counts"]["US"] == 2
