"""Approvals (FR-010, ADR-006)."""

from fastapi import APIRouter, HTTPException

from panos_core.approvals import ApprovalError
from panos_core.domain.models import ActionApproval
from panos_node_api.routers.deps import LOCAL_USER, Container
from panos_node_api.schemas import ApprovalList, ApprovalView, DecisionRequest, DecisionResponse

router = APIRouter(tags=["approvals"])


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


@router.get("/approvals", response_model=ApprovalList)
async def list_approvals(container: Container) -> ApprovalList:
    pending = await container.approvals.list_pending()
    return ApprovalList(approvals=[_approval_view(a) for a in pending])


@router.get("/approvals/{approval_id}", response_model=ApprovalView)
async def get_approval(approval_id: str, container: Container) -> ApprovalView:
    approval = await container.approvals.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return _approval_view(approval)


@router.post("/approvals/{approval_id}/decision", response_model=DecisionResponse)
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
