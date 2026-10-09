"""Audit log (FR-028, ADR-020)."""

from typing import Annotated

from fastapi import APIRouter, Query

from panos_core.domain.models import AuditEvent
from panos_node_api.routers.deps import Container
from panos_node_api.schemas import AuditEventView, AuditList, ChainVerificationView

router = APIRouter(tags=["audit"])


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


@router.get("/audit", response_model=AuditList)
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


@router.get("/audit/verify", response_model=ChainVerificationView)
async def verify_audit(container: Container) -> ChainVerificationView:
    result = await container.audit.verify_chain()
    return ChainVerificationView(**result.model_dump())
