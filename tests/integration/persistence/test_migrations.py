import sqlite3
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from panos_core.config import PanosSettings
from panos_core.persistence.engine import create_sync_engine, downgrade, run_migrations
from panos_core.persistence.orm import Base

EXPECTED_TABLES = {
    "node",
    "project",
    "job",
    "job_step",
    "action_approval",
    "audit_event",
    "checkpoint",
    "alembic_version",
}


def _tables(db: Path) -> set[str]:
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def test_upgrade_creates_all_tables(settings: PanosSettings) -> None:
    run_migrations(settings.database_path)
    assert _tables(settings.database_path) >= EXPECTED_TABLES


def test_migrations_match_orm_metadata(settings: PanosSettings) -> None:
    """Fails if someone changes ORM models without adding a migration."""
    run_migrations(settings.database_path)
    engine = create_sync_engine(settings.database_path)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


def test_downgrade_to_base_and_upgrade_again(settings: PanosSettings) -> None:
    run_migrations(settings.database_path)
    downgrade(settings.database_path, "base")
    assert _tables(settings.database_path) <= {"alembic_version"}
    run_migrations(settings.database_path)
    assert _tables(settings.database_path) >= EXPECTED_TABLES


def test_upgrade_is_idempotent(settings: PanosSettings) -> None:
    run_migrations(settings.database_path)
    run_migrations(settings.database_path)
    assert _tables(settings.database_path) >= EXPECTED_TABLES


@pytest.mark.req("NFR-REL-001")
def test_database_uses_wal_journal(settings: PanosSettings) -> None:
    run_migrations(settings.database_path)
    engine = create_sync_engine(settings.database_path)
    with engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        fks = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
    engine.dispose()
    assert mode == "wal"
    assert fks == 1
