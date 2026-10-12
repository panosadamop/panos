"""Hub runtime state shared by the dashboard/API app and the node-link server."""

import time
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from panos_control_plane.db.engine import make_engine, migrate, sessions
from panos_control_plane.material import HubMaterial, database_url, load_material
from panos_core.config import PanosSettings
from panos_core.secrets import SecretStore


@dataclass
class HubState:
    settings: PanosSettings
    secrets: SecretStore
    material: HubMaterial
    engine: AsyncEngine
    db: async_sessionmaker[AsyncSession]
    started: float = field(default_factory=time.monotonic)

    async def close(self) -> None:
        await self.engine.dispose()


async def open_state(
    settings: PanosSettings, secret_store: SecretStore, *, url: str | None = None
) -> HubState:
    """Load key material, migrate the database and connect."""
    material = load_material(settings, secret_store)
    url = url or database_url(settings, secret_store)
    await migrate(url)
    engine = make_engine(url)
    return HubState(settings, secret_store, material, engine, sessions(engine))
