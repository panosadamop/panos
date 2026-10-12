"""Hub operations on nodes: dispatch, control, approval decisions, live queries (M9-04..06).

All of them go over the node link and are decided by the target node (ADR-028 §5–6).
"""

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from panos_control_plane import audit
from panos_control_plane.db.orm import ApprovalMirror, DispatchRow, JobMirror, NodeRow
from panos_control_plane.links import LinkError, LinkManager
from panos_control_plane.nodes import NodeError
from panos_control_plane.state import HubState
from panos_core.remote.protocol import Control, Converse, Decision, Dispatch, Query, QueryName


def _now() -> datetime:
    return datetime.now(UTC)


async def _target(state: HubState, links: LinkManager, ref: str) -> NodeRow:
    async with state.db() as session:
        node = await session.get(NodeRow, ref)
        if node is None:
            found = list(await session.scalars(select(NodeRow).where(NodeRow.name == ref)))
            if len(found) > 1:
                raise NodeError("conflict", f"several nodes are named {ref!r}; use the node id")
            node = found[0] if found else None
    if node is None:
        raise NodeError("not_found", f"no node {ref!r}")
    if node.revoked_at is not None:
        raise NodeError("revoked", f"{node.name} is revoked")
    if not links.online(node.id):
        raise NodeError("offline", f"{node.name} is not connected to the hub")
    return node


async def dispatch(
    state: HubState,
    links: LinkManager,
    *,
    node: str,
    objective: str,
    project: str | None,
    kind: str,
    requested_by: str,
    argv: list[str] | None = None,
    urls: list[str] | None = None,
) -> DispatchRow:
    """Send a job to a node; the node accepts it under its own policy or refuses it."""
    target = await _target(state, links, node)
    msg = Dispatch(
        dispatch_id=f"dsp_{secrets.token_hex(12)}",
        objective=objective,
        project=project,
        kind=kind,  # type: ignore[arg-type]  # validated by the model
        argv=argv,
        urls=urls,
        requested_by=requested_by,
    )
    async with state.db() as session, session.begin():
        row = DispatchRow(
            id=msg.dispatch_id,
            node_id=target.id,
            project=project,
            kind=kind,
            objective=objective,
            requested_by=requested_by,
            created_at=_now(),
            state="sent",
            job_id=None,
            reason=None,
        )
        session.add(row)
        await audit.record(
            session,
            requested_by,
            "dispatch.sent",
            node_id=target.id,
            details={"dispatch_id": row.id, "project": project, "kind": kind},
        )
    try:
        reply = await links.request(target.id, msg)
        accepted, job_id, reason = reply.ok, (reply.data or {}).get("job_id"), reply.error
    except LinkError as exc:
        accepted, job_id, reason = False, None, str(exc)
    async with state.db() as session, session.begin():
        stored = await session.get(DispatchRow, row.id)
        assert stored is not None  # noqa: S101
        stored.state = "accepted" if accepted else "refused"
        stored.job_id = job_id or stored.job_id
        stored.reason = reason
        await audit.record(
            session,
            requested_by,
            f"dispatch.{stored.state}",
            node_id=target.id,
            details={"dispatch_id": row.id, "job_id": job_id, "reason": reason},
        )
        return stored


async def control(
    state: HubState, links: LinkManager, *, job_id: str, action: str, requested_by: str
) -> dict[str, Any]:
    async with state.db() as session:
        job = await session.get(JobMirror, job_id)
    if job is None:
        raise NodeError("not_found", f"no job {job_id!r}")
    await _target(state, links, job.node_id)
    msg = Control(job_id=job_id, action=action, requested_by=requested_by)  # type: ignore[arg-type]
    reply = await _ask(links, job.node_id, msg)
    async with state.db() as session, session.begin():
        await audit.record(
            session, requested_by, f"job.{action}", node_id=job.node_id, details={"job_id": job_id}
        )
    return dict(reply)


async def decide(
    state: HubState,
    links: LinkManager,
    *,
    approval_id: str,
    approve: bool,
    decided_by: str,
    comment: str = "",
) -> dict[str, Any]:
    async with state.db() as session:
        mirror = await session.get(ApprovalMirror, approval_id)
    if mirror is None:
        raise NodeError("not_found", f"no approval {approval_id!r}")
    node = await _target(state, links, mirror.node_id)
    if not node.accepts_remote_approvals:
        raise NodeError("refused", f"{node.name} does not accept approvals from the hub")
    msg = Decision(approval_id=approval_id, approve=approve, comment=comment, decided_by=decided_by)
    data = await _ask(links, node.id, msg)
    async with state.db() as session, session.begin():
        stored = await session.get(ApprovalMirror, approval_id)
        if stored is not None:
            stored.status = str(data.get("status", stored.status))
            stored.updated_at = _now()
        await audit.record(
            session,
            decided_by,
            "approval.decided",
            node_id=node.id,
            details={"approval_id": approval_id, "approve": approve, "comment": comment},
        )
    return data


async def query(
    state: HubState,
    links: LinkManager,
    *,
    node: str,
    name: QueryName,
    params: dict[str, str | int | None] | None = None,
) -> Any:
    target = await _target(state, links, node)
    reply = await links.request(target.id, Query(name=name, params=params or {}))
    if not reply.ok:
        raise NodeError("refused", reply.error or "the node refused the query")
    return reply.data


async def _ask(links: LinkManager, node_id: str, msg: Any) -> dict[str, Any]:
    try:
        reply = await links.request(node_id, msg)
    except LinkError as exc:
        raise NodeError("offline", str(exc)) from exc
    if not reply.ok:
        raise NodeError("refused", reply.error or "the node refused")
    return dict(reply.data or {})


CONVERSE_WAIT_S = 600.0  # local models on a CPU can take minutes


async def converse(
    state: HubState,
    links: LinkManager,
    *,
    node: str,
    text: str,
    project: str | None,
    conversation_id: str | None,
    requested_by: str,
) -> dict[str, Any]:
    """Relay a message to a PC's conversation (ADR-030 §3). Only metadata is audited."""
    target = await _target(state, links, node)
    msg = Converse(
        text=text, project=project, conversation_id=conversation_id, requested_by=requested_by
    )
    try:
        reply = await links.request(target.id, msg, wait_s=CONVERSE_WAIT_S)
    except LinkError as exc:
        raise NodeError("offline", str(exc)) from exc
    async with state.db() as session, session.begin():
        await audit.record(
            session,
            requested_by,
            "conversation.relayed",
            node_id=target.id,
            details={"ok": reply.ok, "project": project, "chars": len(text)},
        )
    if not reply.ok:
        raise NodeError("refused", reply.error or "the PC could not answer")
    return dict(reply.data or {})
