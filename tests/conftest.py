"""Shared fixtures. Every test runs with isolated per-user directories."""

import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from panos_core.config import PanosSettings, ensure_directories, load_settings
from panos_core.persistence import UnitOfWorkFactory, create_engine, run_migrations

pytest_plugins = ["tests.plugins.req_coverage"]

# Fixture repositories contain their own (fake) test files: never collect them.
collect_ignore_glob = ["fixtures/*"]

_DIR_ENV = {
    "PANOS_DATA_DIR": "data",
    "PANOS_CONFIG_DIR": "config",
    "PANOS_LOG_DIR": "logs",
    "PANOS_RUNTIME_DIR": "run",
    "PANOS_WORKSPACE_DIR": "workspace",
}


@pytest.fixture(autouse=True)
def isolated_env(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point all Panos directories and the config file at a temp dir; never touch real ones."""
    home = tmp_path / "panos-home"
    for name, sub in _DIR_ENV.items():
        monkeypatch.setenv(name, str(home / sub))
    # No real model providers in tests (no Ollama/cloud traffic); tests inject mocks.
    # The file lives outside tmp_path, which some tests use as a project directory.
    config_file = tmp_path_factory.mktemp("panos-config") / "config.toml"
    config_file.write_text("[ai]\nproviders = []\n", encoding="utf-8")
    monkeypatch.setenv("PANOS_CONFIG_FILE", str(config_file))
    # Never touch the real OS credential store from tests.
    monkeypatch.setenv("PANOS_SECRET_BACKEND", "memory")
    for var in ("PANOS_LOG_LEVEL", "PANOS_ALLOWED_ORIGINS", "PANOS_LOG_JSON_CONSOLE"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def settings(isolated_env: Path) -> PanosSettings:
    s = load_settings()
    ensure_directories(s)
    return s


@pytest.fixture
async def uow_factory(settings: PanosSettings) -> AsyncIterator[UnitOfWorkFactory]:
    run_migrations(settings.database_path)
    engine = create_engine(settings.database_path)
    try:
        yield UnitOfWorkFactory(engine)
    finally:
        await engine.dispose()


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("windows") and sys.platform != "win32":
        pytest.skip("Windows-only test")
    if item.get_closest_marker("posix") and sys.platform == "win32":
        pytest.skip("POSIX-only test")


@pytest.fixture(scope="session")
def pg_admin_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A PostgreSQL server for hub tests: ``PANOS_TEST_PG_URL`` or an embedded one (M9)."""
    url = os.environ.get("PANOS_TEST_PG_URL")
    if url:
        yield url
        return
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(tmp_path_factory.mktemp("pg"), cleanup_mode="stop")
    try:
        yield str(server.get_uri())
    finally:
        server.cleanup()
