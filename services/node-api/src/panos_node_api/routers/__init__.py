"""``/api/v1`` routers."""

from fastapi import APIRouter

from panos_core import API_VERSION
from panos_node_api.routers import (
    ai,
    approvals,
    apps,
    audit,
    connectors,
    conversation,
    events,
    jobs,
    knowledge,
    node,
    projects,
    requirements,
    screen,
    voice,
)

api_router = APIRouter(prefix=f"/api/{API_VERSION}")
for module in (
    node,
    approvals,
    audit,
    projects,
    apps,
    conversation,
    ai,
    events,
    jobs,
    requirements,
    voice,
    screen,
    connectors,
    knowledge,
):
    api_router.include_router(module.router)

__all__ = ["api_router"]
