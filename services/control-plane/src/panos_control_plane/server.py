"""Run the hub: HTTPS API/dashboard and the mTLS node link in one process (ADR-028 §2)."""

import asyncio
import contextlib
import socket
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI

from panos_control_plane import api, audit
from panos_control_plane.app import create_app
from panos_control_plane.auth import admin_token_path, write_admin_token
from panos_control_plane.links import LinkManager
from panos_control_plane.material import renew_server_if_needed
from panos_control_plane.state import HubState, open_state
from panos_core.config import PanosSettings
from panos_core.observability import get_logger
from panos_core.secrets import SecretStore

log = get_logger(__name__)


@dataclass
class RunningHub:
    state: HubState
    links: LinkManager
    app: FastAPI
    api_port: int
    link_port: int
    admin_token: str
    _server: uvicorn.Server
    _task: asyncio.Task[None]

    async def stop(self) -> None:
        self._server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._task, 15)
        await self.links.close()
        admin_token_path(self.state).unlink(missing_ok=True)
        await self.state.close()


def build_app(state: HubState, links: LinkManager, admin_token: str) -> FastAPI:
    app = create_app(state)
    app.state.links = links
    app.state.admin_token = admin_token
    app.include_router(api.router)
    from panos_control_plane import dashboard, passkeys  # local: optional static files

    app.include_router(passkeys.router)
    dashboard.mount(app)
    return app


async def start_hub(
    state: HubState, *, host: str, api_port: int, link_port: int, sweep_s: float = 5.0
) -> RunningHub:
    links = LinkManager(state)
    bound_link = await links.start(host, link_port, sweep_s=sweep_s)
    token = write_admin_token(state)
    app = build_app(state, links, token)
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, api_port))
    m = state.material
    config = uvicorn.Config(
        app,
        log_config=None,
        access_log=False,
        ssl_certfile=str(m.server_cert),
        ssl_keyfile=str(m.server_key),
        ssl_keyfile_password=m.passphrase,
        server_header=False,
        proxy_headers=False,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[sock]), name="hub-api")
    while not server.started:
        if task.done():
            await task  # raise the startup error
        await asyncio.sleep(0.05)
    async with state.db() as session, session.begin():
        await audit.record(session, "hub", "hub.started", details={"host": host})
    log.info("hub_started", api=f"https://{host}:{sock.getsockname()[1]}", link_port=bound_link)
    return RunningHub(
        state, links, app, int(sock.getsockname()[1]), bound_link, token, server, task
    )


async def serve(settings: PanosSettings, secret_store: SecretStore) -> None:
    """``panos hub serve``: run until interrupted."""
    if renew_server_if_needed(settings, secret_store):
        log.info("hub_server_certificate_renewed")
    state = await open_state(settings, secret_store)
    host = settings.hub.bind_host or "0.0.0.0"  # noqa: S104 - LAN service; firewall: private networks
    hub = await start_hub(
        state, host=host, api_port=settings.hub.api_port, link_port=settings.hub.link_port
    )
    try:
        await asyncio.Event().wait()
    finally:
        await hub.stop()
