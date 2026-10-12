"""Hub database engine and migrations (PostgreSQL via asyncpg)."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

MIGRATIONS = Path(__file__).parent / "migrations"


def async_url(url: str) -> str:
    """Accept ``postgresql://`` URLs and use the asyncpg driver."""
    for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url.removeprefix(prefix)
    raise ValueError("the hub database must be PostgreSQL (postgresql://…)")


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", async_url(url).replace("%", "%%"))
    return cfg


def run_migrations(url: str, revision: str = "head") -> None:
    """Upgrade the hub schema (blocking: call from a thread in async code)."""
    command.upgrade(alembic_config(url), revision)


async def migrate(url: str) -> None:
    await asyncio.to_thread(run_migrations, url)


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(async_url(url), pool_pre_ping=True)


def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
