"""Node health and capabilities."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from panos_core import API_VERSION, __version__
from panos_core.observability import get_logger
from panos_node_api.routers.deps import Container
from panos_node_api.schemas import CapabilitiesResponse, HealthResponse, NodeInfo

log = get_logger(__name__)
router = APIRouter(tags=["node"])

# Features exposed by this node build. Grows milestone by milestone.
FEATURES: tuple[str, ...] = (
    "health",
    "capabilities",
    "approvals",
    "audit",
    "projects",
    "analysis",
    "apps",
    "knowledge",
)


@router.get("/health", response_model=HealthResponse)
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


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(container: Container) -> CapabilitiesResponse:
    node = container.node
    return CapabilitiesResponse(
        api_version=API_VERSION,
        version=__version__,
        node=NodeInfo(id=node.id, name=node.name, platform=node.platform),
        features=list(FEATURES),
        tools=container.registry.names(),
        desktop=container.apps.desktop.capabilities(),
    )
