"""Hub tests on a real PostgreSQL: an embedded server per session, a fresh database per test.

CI or a developer can point ``PANOS_TEST_PG_URL`` at an existing PostgreSQL instead.
"""

import asyncio
import secrets
import ssl
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from panos_control_plane.material import init_hub
from panos_control_plane.server import RunningHub, start_hub
from panos_control_plane.state import HubState, open_state
from panos_core.config import PanosSettings
from panos_core.secrets import InMemorySecretStore


@pytest.fixture
async def pg_url(pg_admin_url: str) -> AsyncIterator[str]:
    name = f"hub_{secrets.token_hex(6)}"
    admin = await asyncpg.connect(pg_admin_url)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    base, _, _ = pg_admin_url.rpartition("/")
    yield f"{base}/{name}"
    admin = await asyncpg.connect(pg_admin_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


@pytest.fixture
def hub_secrets() -> InMemorySecretStore:
    return InMemorySecretStore()


@pytest.fixture
async def hub(
    settings: PanosSettings, hub_secrets: InMemorySecretStore, pg_url: str
) -> AsyncIterator[HubState]:
    init_hub(
        settings, hub_secrets, hostname="localhost", addresses=["127.0.0.1"], database_url=pg_url
    )
    state = await open_state(settings, hub_secrets)
    try:
        yield state
    finally:
        await state.close()


def hub_dir_of(settings: PanosSettings) -> Path:
    return settings.data_dir / "hub"


@pytest.fixture
async def running(hub: HubState) -> AsyncIterator[RunningHub]:
    running = await start_hub(hub, host="127.0.0.1", api_port=0, link_port=0, sweep_s=0.2)
    hub.settings.hub.api_port = running.api_port  # tests bind port 0
    hub.settings.hub.link_port = running.link_port
    try:
        yield running
    finally:
        await running.stop()


def node_settings(settings: PanosSettings, tmp_path: Path, name: str) -> PanosSettings:
    base = tmp_path / name
    s = settings.model_copy(deep=True)
    s.data_dir, s.runtime_dir = base / "data", base / "run"
    s.remote.heartbeat_s = 0.2
    s.remote.reconnect_max_s = 0.5
    return s


def admin(running: RunningHub) -> httpx.AsyncClient:
    ctx = ssl.create_default_context(cadata=running.state.material.ca_pem.decode())
    return httpx.AsyncClient(
        base_url=f"https://localhost:{running.api_port}/api/v1",
        verify=ctx,
        headers={"Authorization": f"Bearer {running.admin_token}"},
    )


async def wait_for(predicate: Any, limit_s: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + limit_s
    while not await predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition not met in time"
        await asyncio.sleep(0.05)
