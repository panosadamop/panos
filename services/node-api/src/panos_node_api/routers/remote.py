"""This node's hub membership (ADR-028/029, M9-02/03): status, join, leave."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from panos_core.remote import membership
from panos_node_api.routers.deps import LOCAL_USER, Container, raise_for_reason

router = APIRouter(prefix="/remote", tags=["remote"])


class RemoteStatus(BaseModel):
    joined: bool
    hub_url: str | None = None
    node_id: str
    name: str | None = None
    link: str  # disabled | connecting | connected | disconnected | revoked
    last_error: str | None = None
    certificate_expires: str | None = None
    ca_fingerprint: str | None = None
    accept_remote_approvals: bool


class JoinIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hub_url: str = Field(min_length=3, max_length=255, examples=["https://hubpc:48700"])
    code: str = Field(min_length=8, max_length=64)
    fingerprint: str = Field(min_length=64, max_length=100)
    name: str | None = Field(default=None, max_length=128, description="default: the node name")


def _status(container: Any) -> RemoteStatus:
    info = membership.load(container.settings)
    link = container.hub_link
    return RemoteStatus(
        joined=info is not None,
        hub_url=info.hub_url if info else None,
        node_id=container.node.id,
        name=info.name if info else None,
        link=link.state,
        last_error=link.last_error,
        certificate_expires=info.not_after if info else None,
        ca_fingerprint=info.ca_fingerprint if info else None,
        accept_remote_approvals=container.settings.remote.accept_remote_approvals,
    )


@router.get("", response_model=RemoteStatus)
async def remote_status(container: Container) -> Any:
    return _status(container)


@router.post("/join", response_model=RemoteStatus)
async def join(body: JoinIn, container: Container) -> Any:
    if membership.load(container.settings) is not None:
        raise_for_reason("conflict", "this node already belongs to a hub; leave it first")
    name = body.name or container.node.name
    try:
        await membership.join(
            container.settings,
            container.secrets,
            hub_url=body.hub_url,
            code=body.code,
            fingerprint=body.fingerprint,
            node_id=container.node.id,
            name=name,
            platform=container.node.platform,
        )
    except membership.MembershipError as exc:
        raise_for_reason("upstream" if exc.reason == "unreachable" else "invalid", str(exc))
    await container.audit.record(
        actor=LOCAL_USER,
        event_type="remote.joined",
        action="remote.join",
        metadata={"hub": body.hub_url, "name": name},
    )
    container.start_remote()
    return _status(container)


@router.post("/leave", response_model=RemoteStatus)
async def leave(container: Container) -> Any:
    await container.stop_remote()
    if membership.leave(container.settings, container.secrets):
        await container.audit.record(
            actor=LOCAL_USER, event_type="remote.left", action="remote.leave", metadata={}
        )
    return _status(container)
