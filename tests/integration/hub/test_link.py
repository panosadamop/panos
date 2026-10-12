"""Hub ↔ node over real TLS on loopback (M9-02..06; ADR-028/029; AC-US060/061/062)."""

import asyncio
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from panos_control_plane import nodes, operations
from panos_control_plane.db.orm import ApprovalMirror, DispatchRow, JobMirror, NodeCert, NodeRow
from panos_control_plane.server import RunningHub
from panos_core.config import PanosSettings
from panos_core.remote import membership, pki
from panos_core.remote.link import HubLink, RemoteRefusalError
from panos_core.remote.protocol import (
    ApprovalUpdate,
    Control,
    Converse,
    Decision,
    Dispatch,
    Hello,
    Inventory,
    JobUpdate,
    ProjectInfo,
    Query,
    StepInfo,
)
from panos_core.secrets import InMemorySecretStore
from tests.integration.hub.conftest import admin, node_settings, wait_for

pytestmark = pytest.mark.req("BR-024", "BR-025", "FR-026", "FR-027", "NFR-SEC-006")

NODE_A = "nod_" + "a" * 32
NOW = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)


@dataclass
class FakeNode:
    node_id: str = NODE_A
    accepts: bool = True
    dispatched: list[Dispatch] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    controls: list[Control] = field(default_factory=list)

    async def hello(self) -> Hello:
        return Hello(
            node_id=self.node_id,
            name="laptop",
            platform="linux",
            version="0.1.0",
            api_version="v1",
            tools=["fs.read"],
            accepts_remote_approvals=self.accepts,
        )

    async def inventory(self) -> Inventory:
        return Inventory(projects=[ProjectInfo(id="prj_1", name="shop", privacy_mode="LOCAL_ONLY")])

    async def snapshot(self) -> list[JobUpdate | ApprovalUpdate]:
        return [
            JobUpdate(
                job_id="job_1",
                kind="coding",
                objective="Add checkout",
                status="RUNNING",
                status_reason="implementing secret_algo()",
                project_id="prj_1",
                project_name="shop",
                privacy_mode="LOCAL_ONLY",
                created_at=NOW,
                steps=[
                    StepInfo(
                        sequence=1,
                        agent="agent:dev",
                        status="SUCCEEDED",
                        summary="edited src/secret_algo.py",
                    )
                ],
            ),
            ApprovalUpdate(
                approval_id="apr_1",
                job_id="job_1",
                project_id="prj_1",
                action_type="git.commit",
                target="C:/p/shop",
                payload_hash="a" * 64,
                summary="Commit: secret_algo.py",
                status="PENDING",
                privacy_mode="LOCAL_ONLY",
                requested_at=NOW,
            ),
        ]

    async def running_jobs(self) -> int:
        return 1

    async def dispatch(self, msg: Dispatch) -> dict[str, Any]:
        if msg.project == "unknown":
            raise RemoteRefusalError("this PC has no project 'unknown'")
        self.dispatched.append(msg)
        return {"job_id": "job_2"}

    async def control(self, msg: Control) -> dict[str, Any]:
        self.controls.append(msg)
        return {"job_id": msg.job_id, "status": "PAUSED"}

    async def decision(self, msg: Decision) -> dict[str, Any]:
        self.decisions.append(msg)
        return {"approval_id": msg.approval_id, "status": "APPROVED" if msg.approve else "REJECTED"}

    async def converse(self, msg: Converse) -> dict[str, Any]:
        return {"conversation_id": "cnv_1", "text": f"echo: {msg.text}", "privacy": "LOCAL_ONLY"}

    async def query(self, msg: Query) -> Any:
        if msg.name == "job":
            return {
                "job_id": msg.params["job_id"],
                "steps": [{"summary": "edited src/secret_algo.py"}],
            }
        return {"query": msg.name}


async def join(
    running: RunningHub, ns: PanosSettings, store: InMemorySecretStore, node_id: str = NODE_A
) -> None:
    code, _ = await nodes.create_code(running.state, "enroll", label="laptop")
    await membership.join(
        ns,
        store,
        hub_url=f"https://localhost:{running.api_port}",
        code=code,
        fingerprint=running.state.material.fingerprint,
        node_id=node_id,
        name="laptop",
        platform="linux",
    )


@pytest.mark.req("AC-US061-01")
async def test_join_pins_the_ca_and_uses_codes_once(
    running: RunningHub, settings: PanosSettings, tmp_path: Path
) -> None:
    ns, store = node_settings(settings, tmp_path, "a"), InMemorySecretStore()
    code, _ = await nodes.create_code(running.state, "enroll")
    url = f"https://localhost:{running.api_port}"
    with pytest.raises(membership.MembershipError) as wrong_fp:
        await membership.join(
            ns,
            store,
            hub_url=url,
            code=code,
            fingerprint="SHA256:" + "0" * 64,
            node_id=NODE_A,
            name="a",
            platform="linux",
        )
    assert wrong_fp.value.reason == "fingerprint"
    fp = running.state.material.fingerprint
    with pytest.raises(membership.MembershipError, match="refused"):
        await membership.join(
            ns,
            store,
            hub_url=url,
            code="AAAA-BBBB-CCCC",
            fingerprint=fp,
            node_id=NODE_A,
            name="a",
            platform="linux",
        )
    joined = await membership.join(
        ns,
        store,
        hub_url=url,
        code=code,
        fingerprint=fp,
        node_id=NODE_A,
        name="a",
        platform="linux",
    )
    assert joined.link_url == f"wss://localhost:{running.link_port}/link"
    assert store.get(membership.KEY_SECRET) and (membership.remote_dir(ns) / "node.crt").exists()
    with pytest.raises(membership.MembershipError, match="refused"):  # single use
        await membership.join(
            ns,
            store,
            hub_url=url,
            code=code,
            fingerprint=fp,
            node_id=NODE_A,
            name="a",
            platform="linux",
        )
    async with admin(running) as c:
        events = [e["event_type"] for e in (await c.get("/audit")).json()]
    assert events.count("node.enroll_refused") == 2 and "node.enrolled" in events


@pytest.mark.req("AC-US060-01", "AC-US061-02", "AC-US061-03", "AC-US062-02", "NFR-PRV-001")
async def test_link_mirrors_dispatches_and_relays_decisions(
    running: RunningHub, settings: PanosSettings, tmp_path: Path
) -> None:
    ns, store = node_settings(settings, tmp_path, "a"), InMemorySecretStore()
    await join(running, ns, store)
    fake = FakeNode()
    link = HubLink(ns, store, fake)
    assert link.start()
    await asyncio.wait_for(link.connected.wait(), 10)
    state, links = running.state, running.links

    async def mirrored() -> bool:
        async with state.db() as s:
            return await s.get(ApprovalMirror, "apr_1") is not None

    await wait_for(mirrored)
    async with state.db() as s:
        node = await s.get(NodeRow, NODE_A)
        job = await s.get(JobMirror, "job_1")
        approval = await s.get(ApprovalMirror, "apr_1")
    assert node is not None and node.platform == "linux" and node.tools == ["fs.read"]
    assert node.projects[0]["name"] == "shop"
    # LOCAL_ONLY: no step summaries, status reasons or approval summaries are stored.
    assert job is not None and job.steps[0]["summary"] is None and job.status_reason is None
    assert approval is not None and approval.summary is None and approval.payload_hash == "a" * 64

    async with admin(running) as c:
        listed = (await c.get("/nodes")).json()
        assert [(n["id"], n["status"]) for n in listed] == [(NODE_A, "online")]
        sent = await c.post(
            "/dispatch",
            json={
                "node": "laptop",
                "objective": "Run the tests",
                "project": "shop",
                "kind": "coding",
            },
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["state"] == "accepted" and sent.json()["job_id"] == "job_2"
        refused = (
            await c.post(
                "/dispatch", json={"node": NODE_A, "objective": "Do it", "project": "unknown"}
            )
        ).json()
        assert refused["state"] == "refused" and "no project" in refused["reason"]
        detail = (await c.get("/jobs/job_1")).json()  # live view shows what is not stored
        assert detail["live"]["steps"][0]["summary"] == "edited src/secret_algo.py"
        decided = await c.post("/approvals/apr_1/decision", json={"decision": "approve"})
        assert decided.status_code == 200 and decided.json()["status"] == "APPROVED"
        paused = await c.post("/jobs/job_1/pause")
        assert paused.json()["status"] == "PAUSED"
        usage = await c.get(f"/nodes/{NODE_A}/usage")
        assert usage.json() == {"query": "usage"}
        assert (await c.get("/nodes/laptop/files")).status_code == 422  # no such view
    assert fake.dispatched[0].requested_by == "hub-admin" and fake.dispatched[0].project == "shop"
    assert fake.decisions[0].decided_by == "hub-admin" and fake.controls[0].action == "pause"
    async with state.db() as s:
        rows = list(await s.scalars(select(DispatchRow)))
    assert {r.state for r in rows} == {"accepted", "refused"}
    await link.stop()

    async def offline() -> bool:
        return not links.online(NODE_A)

    await wait_for(offline)
    with pytest.raises(nodes.NodeError) as gone:
        await operations.dispatch(
            state,
            links,
            node=NODE_A,
            objective="x y z",
            project=None,
            kind="research",
            requested_by="t",
        )
    assert gone.value.reason == "offline"


async def test_remote_approvals_can_be_refused_by_the_node(
    running: RunningHub, settings: PanosSettings, tmp_path: Path
) -> None:
    ns, store = node_settings(settings, tmp_path, "a"), InMemorySecretStore()
    await join(running, ns, store)
    link = HubLink(ns, store, FakeNode(accepts=False))
    link.start()
    await asyncio.wait_for(link.connected.wait(), 10)

    async def mirrored() -> bool:
        async with running.state.db() as s:
            return await s.get(ApprovalMirror, "apr_1") is not None

    await wait_for(mirrored)
    async with admin(running) as c:
        r = await c.post("/approvals/apr_1/decision", json={"decision": "approve"})
    assert r.status_code == 409 and "does not accept approvals" in r.text
    await link.stop()


@pytest.mark.req("AC-US061-01")
async def test_revocation_closes_the_link_and_stops_reconnects(
    running: RunningHub, settings: PanosSettings, tmp_path: Path
) -> None:
    ns, store = node_settings(settings, tmp_path, "a"), InMemorySecretStore()
    await join(running, ns, store)
    link = HubLink(ns, store, FakeNode())
    link.start()
    await asyncio.wait_for(link.connected.wait(), 10)
    # Revoked by another process (the CLI writes the database directly): the sweep closes it.
    await nodes.revoke(running.state, "laptop", actor="cli")

    async def revoked() -> bool:
        return link.state == "revoked"

    await wait_for(revoked)
    assert not running.links.online(NODE_A)
    async with admin(running) as c:
        listed = (await c.get("/nodes")).json()
        assert listed[0]["status"] == "revoked"
        code = (await c.post("/codes", json={"purpose": "enroll"})).json()["code"]
    with pytest.raises(membership.MembershipError, match="revoked"):  # same identity: refused
        await membership.join(
            ns,
            store,
            hub_url=f"https://localhost:{running.api_port}",
            code=code,
            fingerprint=running.state.material.fingerprint,
            node_id=NODE_A,
            name="laptop",
            platform="linux",
        )
    await link.stop()


async def test_certificate_renewal_over_the_link(
    running: RunningHub, settings: PanosSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ns, store = node_settings(settings, tmp_path, "a"), InMemorySecretStore()
    await join(running, ns, store)
    old = pki.serial_hex(pki.load_cert((membership.remote_dir(ns) / "node.crt").read_bytes()))
    monkeypatch.setattr("panos_core.remote.link.pki.needs_renewal", lambda cert: True)
    link = HubLink(ns, store, FakeNode())
    link.start()

    async def renewed() -> bool:
        cert = pki.load_cert((membership.remote_dir(ns) / "node.crt").read_bytes())
        return pki.serial_hex(cert) != old

    await wait_for(renewed)
    async with running.state.db() as s:
        retired = await s.get(NodeCert, old)
    assert retired is not None and retired.retired_at is not None
    await link.stop()


async def test_api_requires_authentication(running: RunningHub) -> None:
    ctx = ssl.create_default_context(cadata=running.state.material.ca_pem.decode())
    async with httpx.AsyncClient(base_url=f"https://localhost:{running.api_port}", verify=ctx) as c:
        assert (await c.get("/api/v1/health")).status_code == 200
        assert (await c.get("/api/v1/nodes")).status_code == 401
        bad = await c.get("/api/v1/nodes", headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401
        page = await c.get("/")
        assert page.headers["content-security-policy"].startswith("default-src 'self'")
        assert page.headers["x-frame-options"] == "DENY"
