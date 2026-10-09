"""``/api/v1`` routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from panos_core import API_VERSION, __version__
from panos_core.approvals import ApprovalError
from panos_core.domain.models import ActionApproval, AuditEvent
from panos_core.observability import get_logger
from panos_node_api.container import NodeContainer
from panos_node_api.schemas import (
    ApprovalList,
    ApprovalView,
    AuditEventView,
    AuditList,
    CapabilitiesResponse,
    ChainVerificationView,
    DecisionRequest,
    DecisionResponse,
    HealthResponse,
    NodeInfo,
)

log = get_logger(__name__)

router = APIRouter(prefix=f"/api/{API_VERSION}")

# Features exposed by this node build. Grows milestone by milestone.
FEATURES: tuple[str, ...] = ("health", "capabilities", "approvals", "audit")

# The loopback API authenticates the local user session (ADR-003); decisions are theirs.
LOCAL_USER = "user"


def get_container(request: Request) -> NodeContainer:
    container: NodeContainer = request.app.state.container
    return container


Container = Annotated[NodeContainer, Depends(get_container)]


@router.get("/health", response_model=HealthResponse, tags=["node"])
async def health(container: Container) -> HealthResponse:
    try:
        async with container.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        log.error("health_database_unavailable", error=str(exc))
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return HealthResponse(
        status="ok",
        version=__version__,
        api_version=API_VERSION,
        node_id=container.node.id,
        uptime_s=round(container.uptime_s, 3),
        database="ok",
    )


@router.get("/capabilities", response_model=CapabilitiesResponse, tags=["node"])
async def capabilities(container: Container) -> CapabilitiesResponse:
    node = container.node
    return CapabilitiesResponse(
        api_version=API_VERSION,
        version=__version__,
        node=NodeInfo(id=node.id, name=node.name, platform=node.platform),
        features=list(FEATURES),
        tools=container.registry.names(),
    )


# ------------------------------------------------------------------ approvals
def _approval_view(a: ActionApproval) -> ApprovalView:
    return ApprovalView(
        id=a.id,
        status=a.status,
        action_type=a.action_type,
        target=a.target,
        summary=a.summary,
        payload_hash=a.payload_hash,
        project_id=a.project_id,
        job_id=a.job_id,
        requested_at=a.requested_at,
        expires_at=a.expires_at,
        decided_at=a.decided_at,
        decided_by=a.decided_by,
    )


@router.get("/approvals", response_model=ApprovalList, tags=["approvals"])
async def list_approvals(container: Container) -> ApprovalList:
    pending = await container.approvals.list_pending()
    return ApprovalList(approvals=[_approval_view(a) for a in pending])


@router.get("/approvals/{approval_id}", response_model=ApprovalView, tags=["approvals"])
async def get_approval(approval_id: str, container: Container) -> ApprovalView:
    approval = await container.approvals.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return _approval_view(approval)


@router.post(
    "/approvals/{approval_id}/decision", response_model=DecisionResponse, tags=["approvals"]
)
async def decide_approval(
    approval_id: str, body: DecisionRequest, container: Container
) -> DecisionResponse:
    try:
        result = await container.approvals.decide(
            approval_id,
            approve=body.decision == "approve",
            decided_by=LOCAL_USER,
            comment=body.comment,
        )
    except ApprovalError as exc:
        status = 404 if exc.reason == "not_found" else 409
        raise HTTPException(status_code=status, detail=f"cannot decide: {exc.reason}") from exc
    return DecisionResponse(approval=_approval_view(result.approval), token=result.token)


# ------------------------------------------------------------------ audit
def _audit_view(e: AuditEvent) -> AuditEventView:
    return AuditEventView(
        id=e.id,
        created_at=e.created_at,
        actor=e.actor,
        event_type=e.event_type,
        action=e.action,
        policy_decision=e.policy_decision,
        project_id=e.project_id,
        job_id=e.job_id,
        metadata=e.metadata,
        hash=e.hash,
    )


@router.get("/audit", response_model=AuditList, tags=["audit"])
async def list_audit(
    container: Container,
    project_id: str | None = None,
    job_id: str | None = None,
    event_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> AuditList:
    events = await container.audit.list(
        project_id=project_id, job_id=job_id, event_type=event_type, limit=limit
    )
    return AuditList(events=[_audit_view(e) for e in events])


@router.get("/audit/verify", response_model=ChainVerificationView, tags=["audit"])
async def verify_audit(container: Container) -> ChainVerificationView:
    result = await container.audit.verify_chain()
    return ChainVerificationView(**result.model_dump())
