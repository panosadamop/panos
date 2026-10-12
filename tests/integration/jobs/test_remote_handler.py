"""The node's answers to hub requests, on a real node (M9-04..06, ADR-028 §5–7)."""

import sys
from pathlib import Path

import pytest

from panos_core.domain.enums import JobStatus, PrivacyMode
from panos_core.remote.handler import NodeRemoteHandler, job_update
from panos_core.remote.link import RemoteRefusalError
from panos_core.remote.protocol import Control, Decision, Dispatch, Query
from panos_node_api.container import NodeContainer
from tests.integration.jobs.conftest import calc_project, wait_for

pytestmark = pytest.mark.req("FR-026", "AC-US061-03", "AC-US062-02", "NFR-PRV-001")


def handler(node: NodeContainer) -> NodeRemoteHandler:
    return NodeRemoteHandler(
        settings=node.settings,
        node=node.node,
        projects=node.projects,
        runner=node.runner,
        jobs=node.jobs,
        approvals=node.approvals,
        audit=node.audit,
        requirements=node.requirements,
        uow_factory=node.uow_factory,
        tools=node.registry.names,
        features=["jobs"],
        name="laptop",
    )


def dispatch(objective: str, **kw: object) -> Dispatch:
    return Dispatch(dispatch_id="dsp_1", objective=objective, requested_by="user:panos", **kw)  # type: ignore[arg-type]


async def test_announcements_and_privacy_filter(node: NodeContainer, tmp_path: Path) -> None:
    h = handler(node)
    project = await calc_project(node, tmp_path, git_repo=False)
    hello = await h.hello()
    assert hello.name == "laptop" and "shell.run" in hello.tools and hello.accepts_remote_approvals
    assert [p.name for p in (await h.inventory()).projects] == [project.name]

    job = await node.runner.submit(
        "say hi", kind="command", project=project, input={"argv": [sys.executable, "--version"]}
    )
    job = await wait_for(
        node, job.id, {JobStatus.COMPLETED, JobStatus.WAITING_FOR_APPROVAL, JobStatus.BLOCKED}
    )
    steps = await node.jobs.steps(job.id)
    local = job_update(job, steps, project)
    assert project.privacy_mode == PrivacyMode.LOCAL_ONLY
    assert all(s.summary is None for s in local.steps)  # nothing derived leaves for LOCAL_ONLY
    standard = job_update(
        job, steps, project.model_copy(update={"privacy_mode": PrivacyMode.STANDARD})
    )
    assert any(s.summary for s in standard.steps)
    snapshot = await h.snapshot()
    assert any(getattr(m, "job_id", None) == job.id for m in snapshot)
    assert await h.running_jobs() == 0


async def test_dispatch_is_decided_by_the_node(node: NodeContainer, tmp_path: Path) -> None:
    h = handler(node)
    project = await calc_project(node, tmp_path, git_repo=False)
    for bad, reason in (
        (dispatch("Do it", project="missing"), "no project"),
        (dispatch("Write code", kind="coding"), "needs a project"),
        (dispatch("Run it", kind="command", project=project.name), "needs argv"),
    ):
        with pytest.raises(RemoteRefusalError, match=reason):
            await h.dispatch(bad)
    accepted = await h.dispatch(
        dispatch(
            "Version", kind="command", project=project.name, argv=[sys.executable, "--version"]
        )
    )
    job = await node.jobs.get(accepted["job_id"])
    assert job.input["dispatch_id"] == "dsp_1" and job.input["requested_by"] == "user:panos"
    events = await node.audit.list(event_type="remote.dispatch_accepted")
    assert events and events[0].actor == "remote:user:panos"
    await wait_for(
        node, job.id, {JobStatus.COMPLETED, JobStatus.WAITING_FOR_APPROVAL, JobStatus.BLOCKED}
    )
    with pytest.raises(RemoteRefusalError):
        await h.control(Control(job_id="job_missing", action="pause", requested_by="u"))


async def test_remote_decisions_and_queries(node: NodeContainer, tmp_path: Path) -> None:
    h = handler(node)
    project = await calc_project(node, tmp_path, git_repo=False)
    (Path(project.path) / "hello.py").write_bytes(b"print('hi')\n")
    job = await node.runner.submit(
        "run hello", kind="command", project=project, input={"argv": [sys.executable, "hello.py"]}
    )
    job = await wait_for(node, job.id, {JobStatus.WAITING_FOR_APPROVAL})
    [approval] = await node.approvals.list_pending()
    interactive = await node.approvals.request(
        action_type="fs.write",
        target=project.path,
        payload={"path": "notes.md"},
        summary="write a file",
        actor="user",
    )
    with pytest.raises(RemoteRefusalError, match="interactive"):
        await h.decision(Decision(approval_id=interactive.id, approve=True, decided_by="u"))

    live = await h.query(Query(name="approval", params={"approval_id": approval.id}))
    assert "hello.py" in live["summary"]  # the live view carries what the mirror does not
    node.settings.remote.accept_remote_approvals = False
    with pytest.raises(RemoteRefusalError, match="does not accept"):
        await h.decision(Decision(approval_id=approval.id, approve=True, decided_by="u"))
    node.settings.remote.accept_remote_approvals = True
    done = await h.decision(
        Decision(approval_id=approval.id, approve=True, decided_by="user:panos")
    )
    assert done["status"] == "APPROVED"
    job = await wait_for(node, job.id, {JobStatus.COMPLETED})
    detail = await h.query(Query(name="job", params={"job_id": job.id}))
    assert detail["status"] == "COMPLETED" and detail["result"]["success"] is True
    assert "stdout" not in detail["result"]  # bulky tool output stays on the node

    audit = await h.query(Query(name="audit", params={"limit": 5}))
    assert len(audit) == 5 and {"event_type", "actor"} <= set(audit[0])
    usage = await h.query(Query(name="usage", params={"days": 7}))
    assert usage["total_cost_usd"] >= 0 and isinstance(usage["rows"], list)
    trace = await h.query(Query(name="traceability", params={"project": project.name}))
    assert isinstance(trace, dict)
    projects = await h.query(Query(name="projects"))
    assert projects[0]["name"] == project.name
    for bad in (
        Query(name="traceability"),
        Query(name="job", params={"job_id": "job_missing"}),
        Query(name="approval", params={"approval_id": "apr_missing"}),
    ):
        with pytest.raises(RemoteRefusalError):
            await h.query(bad)
