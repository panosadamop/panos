"""FastAPI application factory."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from panos_core import API_VERSION, __version__
from panos_core.config import PanosSettings
from panos_node_api.container import build_container
from panos_node_api.correlation import CorrelationIdMiddleware
from panos_node_api.problems import install_problem_handlers
from panos_node_api.routes import router
from panos_node_api.security import AuthConfig, LoopbackSecurityMiddleware


def create_app(
    settings: PanosSettings,
    auth: AuthConfig,
    *,
    on_shutdown: Callable[[], None] | None = None,
) -> FastAPI:
    """Build the node API.

    Interactive docs and the live ``/openapi.json`` endpoint are disabled: they would
    sit outside authentication. The schema is exported with ``scripts/export_openapi.py``.

    ``on_shutdown`` runs at the end of the lifespan. The daemon uses it for cleanup
    because uvicorn re-raises the shutdown signal afterwards, and on Windows a
    re-raised SIGBREAK terminates the process before any ``finally`` block runs.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = await build_container(settings)
        app.state.container = container
        try:
            yield
        finally:
            await container.close()
            if on_shutdown is not None:
                on_shutdown()

    app = FastAPI(
        title="Panos Local Node API",
        version=f"{API_VERSION} ({__version__})",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    install_problem_handlers(app)
    app.include_router(router)
    # Starlette wraps in reverse order: the last middleware added is the outermost.
    app.add_middleware(LoopbackSecurityMiddleware, config=auth)
    app.add_middleware(CorrelationIdMiddleware)
    return app
