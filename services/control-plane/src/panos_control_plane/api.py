"""Hub API for the dashboard and the hub CLI (FR-026/027, US-060..062, AC-US062-01/02).

There is no endpoint that reads node files or runs commands: everything below is either hub
metadata or an allowlisted request answered by the node itself.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from panos_control_plane import audit, nodes, operations
from panos_control_plane.app import PREFIX, State, problem
from panos_control_plane.auth import Caller
from panos_control_plane.db.orm import ApprovalMirror, DispatchRow, HubAudit, JobMirror, NodeRow
from panos_control_plane.links import LinkManager
from panos_core.remote import pki

router = APIRouter(prefix=PREFIX)


def get_links(request: Request) -> LinkManager:
    links: LinkManager = request.app.state.links
    return links


Links = Annotated[LinkManager, Depends(get_links)]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# ===================================================================== nodes
class NodeView(BaseModel):
    id: str
    name: str
    platform: str
    version: str
    status: Literal["online", "offline", "revoked"]
    enrolled_at: str
    last_seen_at: str | None
    revoked_at: str | None
    running_jobs: int
    accepts_remote_approvals: bool
    tools: list[str]
    features: list[str]
    projects: list[dict[str, Any]]


def node_view(n: NodeRow, links: LinkManager) -> NodeView:
    status: Literal["online", "offline", "revoked"] = (
        "revoked" if n.revoked_at else "online" if links.online(n.id) else "offline"
    )
    return NodeView(
        id=n.id,
        name=n.name,
        platform=n.platform,
        version=n.version,
        status=status,
        enrolled_at=n.enrolled_at.isoformat(),
        last_seen_at=_iso(n.last_seen_at),
        revoked_at=_iso(n.revoked_at),
        running_jobs=n.running_jobs if status == "online" else 0,
        accepts_remote_approvals=n.accepts_remote_approvals,
        tools=list(n.tools),
        features=list(n.features),
        projects=list(n.projects),
    )


@router.get("/nodes", response_model=list[NodeView])
async def list_nodes(state: State, links: Links, _: Caller) -> Any:
    return [node_view(n, links) for n in await nodes.list_nodes(state)]


@router.post("/nodes/{ref}/revoke", response_model=NodeView)
async def revoke_node(ref: str, state: State, links: Links, who: Caller) -> Any:
    try:
        node = await nodes.revoke(state, ref, actor=who.actor)
    except nodes.NodeError as exc:
        raise problem(exc.reason, str(exc)) from exc
    await links.disconnect(node.id, reason="revoked")
    return node_view(node, links)


class CodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal["enroll", "passkey"] = "enroll"
    label: str = Field(default="", max_length=128)


class CodeOut(BaseModel):
    code: str
    purpose: str
    expires_at: str
    hub_url: str
    ca_fingerprint: str


@router.post("/codes", response_model=CodeOut)
async def create_code(body: CodeIn, request: Request, state: State, who: Caller) -> Any:
    if body.purpose == "passkey" and who.kind != "admin":
        raise HTTPException(status_code=403, detail="passkey codes are created on the hub PC")
    code, expires = await nodes.create_code(state, body.purpose, label=body.label, actor=who.actor)
    host = state.settings.hub.hostname or (state.material.hostnames or ["localhost"])[0]
    return CodeOut(
        code=code,
        purpose=body.purpose,
        expires_at=expires.isoformat(),
        hub_url=f"https://{host}:{state.settings.hub.api_port}",
        ca_fingerprint=pki.fingerprint(state.material.ca_pem),
    )


# ===================================================================== dispatch and jobs
class DispatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str = Field(min_length=1, max_length=128, description="node id or name")
    objective: str = Field(min_length=3, max_length=10_000)
    project: str | None = Field(default=None, max_length=128)
    kind: Literal["coding", "command", "research"] = "coding"
    argv: list[str] | None = Field(default=None, max_length=256)
    urls: list[str] | None = Field(default=None, max_length=10)


class DispatchView(BaseModel):
    id: str
    node_id: str
    project: str | None
    kind: str
    objective: str
    requested_by: str
    created_at: str
    state: str
    job_id: str | None
    reason: str | None


def dispatch_view(d: DispatchRow) -> DispatchView:
    return DispatchView(
        id=d.id,
        node_id=d.node_id,
        project=d.project,
        kind=d.kind,
        objective=d.objective,
        requested_by=d.requested_by,
        created_at=d.created_at.isoformat(),
        state=d.state,
        job_id=d.job_id,
        reason=d.reason,
    )


@router.post("/dispatch", response_model=DispatchView)
async def create_dispatch(body: DispatchIn, state: State, links: Links, who: Caller) -> Any:
    try:
        row = await operations.dispatch(
            state,
            links,
            node=body.node,
            objective=body.objective,
            project=body.project,
            kind=body.kind,
            requested_by=who.actor,
            argv=body.argv,
            urls=body.urls,
        )
    except nodes.NodeError as exc:
        raise problem(exc.reason, str(exc)) from exc
    return dispatch_view(row)


@router.get("/dispatches", response_model=list[DispatchView])
async def list_dispatches(
    state: State, _: Caller, limit: Annotated[int, Query(le=500)] = 100
) -> Any:
    async with state.db() as session:
        rows = await session.scalars(
            select(DispatchRow).order_by(DispatchRow.created_at.desc()).limit(limit)
        )
        return [dispatch_view(r) for r in rows]


class JobView(BaseModel):
    job_id: str
    node_id: str
    kind: str
    objective: str
    status: str
    status_reason: str | None
    phase: str | None
    project_id: str | None
    project_name: str | None
    privacy_mode: str
    dispatch_id: str | None
    created_at: str
    completed_at: str | None
    updated_at: str
    steps: list[dict[str, Any]]


def job_view(j: JobMirror) -> JobView:
    return JobView(
        job_id=j.job_id,
        node_id=j.node_id,
        kind=j.kind,
        objective=j.objective,
        status=j.status,
        status_reason=j.status_reason,
        phase=j.phase,
        project_id=j.project_id,
        project_name=j.project_name,
        privacy_mode=j.privacy_mode,
        dispatch_id=j.dispatch_id,
        created_at=j.created_at.isoformat(),
        completed_at=_iso(j.completed_at),
        updated_at=j.updated_at.isoformat(),
        steps=list(j.steps),
    )


@router.get("/jobs", response_model=list[JobView])
async def list_jobs(
    state: State,
    _: Caller,
    node: str | None = None,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> Any:
    stmt = select(JobMirror).order_by(JobMirror.created_at.desc()).limit(limit)
    if node:
        stmt = stmt.where(JobMirror.node_id == node)
    if status:
        stmt = stmt.where(JobMirror.status == status)
    async with state.db() as session:
        return [job_view(j) for j in await session.scalars(stmt)]


@router.get("/jobs/{job_id}")
async def job_detail(job_id: str, state: State, links: Links, _: Caller) -> dict[str, Any]:
    """Live detail from the node (LOCAL_ONLY summaries are shown, not stored)."""
    async with state.db() as session:
        mirror = await session.get(JobMirror, job_id)
    if mirror is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id!r}")
    view = job_view(mirror).model_dump()
    try:
        live = await operations.query(
            state, links, node=mirror.node_id, name="job", params={"job_id": job_id}
        )
    except nodes.NodeError as exc:
        return {**view, "live": None, "live_error": str(exc)}
    return {**view, "live": live, "live_error": None}


@router.post("/jobs/{job_id}/{action}")
async def control_job(
    job_id: str,
    action: Literal["pause", "resume", "cancel"],
    state: State,
    links: Links,
    who: Caller,
) -> dict[str, Any]:
    try:
        return await operations.control(
            state, links, job_id=job_id, action=action, requested_by=who.actor
        )
    except nodes.NodeError as exc:
        raise problem(exc.reason, str(exc)) from exc


# ===================================================================== approvals
class ApprovalView(BaseModel):
    approval_id: str
    node_id: str
    job_id: str | None
    project_id: str | None
    action_type: str
    target: str
    payload_hash: str
    summary: str | None
    status: str
    privacy_mode: str
    requested_at: str
    expires_at: str | None


def approval_view(a: ApprovalMirror) -> ApprovalView:
    return ApprovalView(
        approval_id=a.approval_id,
        node_id=a.node_id,
        job_id=a.job_id,
        project_id=a.project_id,
        action_type=a.action_type,
        target=a.target,
        payload_hash=a.payload_hash,
        summary=a.summary,
        status=a.status,
        privacy_mode=a.privacy_mode,
        requested_at=a.requested_at.isoformat(),
        expires_at=_iso(a.expires_at),
    )


@router.get("/approvals", response_model=list[ApprovalView])
async def list_approvals(state: State, _: Caller, status: str | None = "PENDING") -> Any:
    stmt = select(ApprovalMirror).order_by(ApprovalMirror.requested_at.desc()).limit(200)
    if status:
        stmt = stmt.where(ApprovalMirror.status == status)
    async with state.db() as session:
        rows = list(await session.scalars(stmt))
    now = datetime.now(UTC)
    return [
        approval_view(a)
        for a in rows
        if a.status != "PENDING" or not a.expires_at or a.expires_at > now
    ]


@router.get("/approvals/{approval_id}")
async def approval_detail(
    approval_id: str, state: State, links: Links, _: Caller
) -> dict[str, Any]:
    async with state.db() as session:
        mirror = await session.get(ApprovalMirror, approval_id)
    if mirror is None:
        raise HTTPException(status_code=404, detail=f"no approval {approval_id!r}")
    view = approval_view(mirror).model_dump()
    try:
        live = await operations.query(
            state, links, node=mirror.node_id, name="approval", params={"approval_id": approval_id}
        )
    except nodes.NodeError as exc:
        return {**view, "live": None, "live_error": str(exc)}
    return {**view, "live": live, "live_error": None}


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=2000)


@router.post("/approvals/{approval_id}/decision")
async def decide(
    approval_id: str, body: DecisionIn, state: State, links: Links, who: Caller
) -> dict[str, Any]:
    try:
        return await operations.decide(
            state,
            links,
            approval_id=approval_id,
            approve=body.decision == "approve",
            decided_by=who.actor,
            comment=body.comment,
        )
    except nodes.NodeError as exc:
        raise problem(exc.reason, str(exc)) from exc


# ===================================================================== live node views
@router.get("/nodes/{ref}/{view}")
async def node_query(
    ref: str,
    view: Literal["audit", "usage", "traceability", "projects", "analytics", "effort"],
    state: State,
    links: Links,
    _: Caller,
    project: str | None = None,
    days: Annotated[int, Query(ge=1, le=366)] = 30,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> Any:
    params: dict[str, str | int | None] = {"project": project, "days": days, "limit": limit}
    try:
        return await operations.query(state, links, node=ref, name=view, params=params)
    except nodes.NodeError as exc:
        raise problem(exc.reason, str(exc)) from exc


# ===================================================================== conversation (ADR-030)
class ConverseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=8000)
    project: str | None = Field(default=None, max_length=128)
    conversation_id: str | None = Field(default=None, max_length=64)


@router.post("/converse")
async def converse(body: ConverseIn, state: State, links: Links, who: Caller) -> dict[str, Any]:
    """Talk to a PC: its own conversation service answers (text is not stored on the hub)."""
    try:
        return await operations.converse(
            state,
            links,
            node=body.node,
            text=body.text,
            project=body.project,
            conversation_id=body.conversation_id,
            requested_by=who.actor,
        )
    except nodes.NodeError as exc:
        raise problem(exc.reason, str(exc)) from exc


# ===================================================================== analytics (M10-06)
_SUMS = ("calls", "input_tokens", "output_tokens", "cost_usd")


@router.get("/analytics")
async def analytics(
    state: State, links: Links, _: Caller, days: Annotated[int, Query(ge=1, le=366)] = 30
) -> dict[str, Any]:
    """Model/cost analytics of every online PC, and combined totals."""
    per_node: list[dict[str, Any]] = []
    for n in await nodes.list_nodes(state):
        if n.revoked_at or not links.online(n.id):
            continue
        try:
            data = await operations.query(
                state, links, node=n.id, name="analytics", params={"days": days}
            )
        except nodes.NodeError:
            continue
        per_node.append({"node_id": n.id, "name": n.name, **data})
    combined: dict[str, Any] = {k: 0 for k in _SUMS}
    by_model: dict[str, dict[str, Any]] = {}
    local_calls = 0
    for d in per_node:
        for k in _SUMS:
            combined[k] = round(combined[k] + d.get(k, 0), 6)
        local_calls += round((d.get("local_share") or 0) * d.get("calls", 0))
        for m in d.get("by_model", []):
            row = by_model.setdefault(m["name"], {"name": m["name"], **{k: 0 for k in _SUMS}})
            for k in _SUMS:
                row[k] = round(row[k] + m.get(k, 0), 6)
    combined["local_share"] = (
        round(local_calls / combined["calls"], 3) if combined["calls"] else None
    )
    combined["by_model"] = sorted(by_model.values(), key=lambda r: -r["cost_usd"])
    return {"days": days, "combined": combined, "nodes": per_node}


# ===================================================================== hub audit
@router.get("/audit")
async def hub_audit(
    state: State, _: Caller, limit: Annotated[int, Query(ge=1, le=1000)] = 200
) -> list[dict[str, Any]]:
    async with state.db() as session:
        rows = await session.scalars(select(HubAudit).order_by(HubAudit.seq.desc()).limit(limit))
        return [
            {
                "seq": r.seq,
                "created_at": r.created_at.isoformat(),
                "actor": r.actor,
                "event_type": r.event_type,
                "node_id": r.node_id,
                "details": r.details,
            }
            for r in rows
        ]


@router.get("/audit/verify")
async def verify_audit(state: State, _: Caller) -> dict[str, Any]:
    async with state.db() as session:
        check = await audit.verify(session)
    return {"ok": check.ok, "checked": check.checked, "first_broken_seq": check.first_broken_seq}


@router.get("/summary")
async def summary(state: State, links: Links, _: Caller) -> dict[str, Any]:
    """Dashboard header: counts per state and the last day's activity."""
    since = datetime.now(UTC) - timedelta(days=1)
    async with state.db() as session:
        all_nodes = list(await session.scalars(select(NodeRow)))
        jobs = list(await session.scalars(select(JobMirror).where(JobMirror.updated_at >= since)))
        pending = list(
            await session.scalars(select(ApprovalMirror).where(ApprovalMirror.status == "PENDING"))
        )
    return {
        "nodes": {
            "online": sum(1 for n in all_nodes if links.online(n.id) and not n.revoked_at),
            "total": sum(1 for n in all_nodes if not n.revoked_at),
        },
        "jobs_last_day": len(jobs),
        "running": sum(1 for j in jobs if j.status == "RUNNING"),
        "pending_approvals": len(pending),
    }
