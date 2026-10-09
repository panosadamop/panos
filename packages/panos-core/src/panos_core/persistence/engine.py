"""Engine creation and migrations."""

from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
BUSY_TIMEOUT_MS = 5000


def _set_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def sqlite_url(path: Path, *, driver: str = "aiosqlite") -> str:
    return f"sqlite+{driver}:///{path.as_posix()}"


def create_engine(database_path: Path) -> AsyncEngine:
    """Create the async engine with WAL, foreign keys and a busy timeout enabled."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(sqlite_url(database_path))
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    return engine


def create_sync_engine(database_path: Path) -> sa.Engine:
    """Synchronous engine used by migrations."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(sqlite_url(database_path, driver="pysqlite"))
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def alembic_config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", sqlite_url(database_path, driver="pysqlite"))
    return config


def run_migrations(database_path: Path, revision: str = "head") -> None:
    """Upgrade the database to ``revision`` (blocking; call via a thread from async code)."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(database_path), revision)


def downgrade(database_path: Path, revision: str) -> None:
    command.downgrade(alembic_config(database_path), revision)
