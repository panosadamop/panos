"""Hub HTTPS app: health, enrollment, dashboard auth and API (ADR-028 §2)."""

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from panos_control_plane import nodes
from panos_control_plane.state import HubState
from panos_core import API_VERSION, __version__

PREFIX = f"/api/{API_VERSION}"


def get_state(request: Request) -> HubState:
    state: HubState = request.app.state.hub
    return state


State = Annotated[HubState, Depends(get_state)]

router = APIRouter(prefix=PREFIX)


@router.get("/health")
async def health(state: State) -> dict[str, Any]:
    async with state.db() as session:
        await session.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "role": "hub",
        "version": __version__,
        "api_version": API_VERSION,
        "uptime_s": round(time.monotonic() - state.started, 3),
        "ca_fingerprint": state.material.fingerprint,
    }


REASON_STATUS = {
    "invalid_code": 403,
    "revoked": 403,
    "unauthorized": 401,
    "invalid": 400,
    "not_found": 404,
    "conflict": 409,
    "offline": 409,
    "refused": 409,
}
MAX_FAILURES_PER_MINUTE = 5


def problem(reason: str, message: str) -> HTTPException:
    return HTTPException(status_code=REASON_STATUS.get(reason, 400), detail=message)


@router.get("/ca", response_class=PlainTextResponse)
async def ca_certificate(state: State) -> str:
    """The hub CA certificate. Public; a joining node checks its fingerprint (ADR-029)."""
    return state.material.ca_pem.decode()


class EnrollIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=8, max_length=64)
    csr: str = Field(min_length=100, max_length=8000)
    node_id: str = Field(min_length=5, max_length=64)
    name: str = Field(default="", max_length=128)
    platform: str = Field(default="", max_length=32)


class EnrollOut(BaseModel):
    node_id: str
    certificate: str
    ca: str
    not_after: str
    link_port: int
    hostnames: list[str]


class _Throttle:
    """Failed enrollment attempts per client address (in memory, per minute)."""

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}

    def check(self, client: str) -> None:
        recent = [t for t in self._failures.get(client, []) if time.monotonic() - t < 60]
        self._failures[client] = recent
        if len(recent) >= MAX_FAILURES_PER_MINUTE:
            raise HTTPException(status_code=429, detail="too many failed attempts; wait a minute")

    def failed(self, client: str) -> None:
        self._failures.setdefault(client, []).append(time.monotonic())


@router.post("/enroll", response_model=EnrollOut)
async def enroll(body: EnrollIn, request: Request, state: State) -> EnrollOut:
    throttle: _Throttle = request.app.state.throttle
    client = request.client.host if request.client else "unknown"
    throttle.check(client)
    try:
        done = await nodes.enroll(
            state,
            code=body.code,
            csr_pem=body.csr.encode(),
            node_id=body.node_id,
            name=body.name,
            platform=body.platform,
            source=client,
        )
    except nodes.NodeError as exc:
        throttle.failed(client)
        raise problem(exc.reason, str(exc)) from exc
    return EnrollOut(
        node_id=done.node_id,
        certificate=done.cert_pem.decode(),
        ca=done.ca_pem.decode(),
        not_after=done.not_after.isoformat(),
        link_port=state.settings.hub.link_port,
        hostnames=state.material.hostnames,
    )


def create_app(state: HubState) -> FastAPI:
    app = FastAPI(title="Panos hub", version=__version__, docs_url=None, redoc_url=None)
    app.state.hub = state
    app.state.throttle = _Throttle()
    app.include_router(router)
    return app
