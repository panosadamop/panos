from pathlib import Path

import pytest
from pydantic import ValidationError

from panos_core.config import ensure_directories, load_settings
from panos_core.config.settings import PanosSettings


def test_isolated_env_points_dirs_at_tmp(isolated_env: Path) -> None:
    s = load_settings()
    assert s.data_dir == (isolated_env / "data").resolve()
    assert s.database_path == s.data_dir / "panos.db"
    assert s.runtime_file.parent == s.runtime_dir


@pytest.mark.req("BR-029", "FR-004")
def test_default_workspace_is_home_panos_projects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("PANOS_WORKSPACE_DIR")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    s = load_settings()
    assert s.workspace_dir == (tmp_path / "Panos" / "Projects").resolve()


@pytest.mark.req("BR-029")
def test_ensure_directories_creates_workspace_and_dirs() -> None:
    s = load_settings()
    ensure_directories(s)
    for d in (s.data_dir, s.config_dir, s.log_dir, s.runtime_dir, s.workspace_dir):
        assert d.is_dir()


def test_precedence_override_beats_env_beats_toml_beats_default(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = isolated_env / "config" / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text('log_level = "WARNING"\nlog_json_console = true\n', encoding="utf-8")

    assert load_settings().log_level == "WARNING"  # TOML beats default
    assert load_settings().log_json_console is True

    monkeypatch.setenv("PANOS_LOG_LEVEL", "ERROR")
    assert load_settings().log_level == "ERROR"  # env beats TOML

    assert load_settings(log_level="DEBUG").log_level == "DEBUG"  # explicit beats env


def test_unknown_settings_are_rejected(isolated_env: Path) -> None:
    config_file = isolated_env / "config" / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("not_a_setting = 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings()


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValidationError):
        PanosSettings(log_level="LOUD")  # type: ignore[arg-type]


def test_relative_dirs_are_resolved_to_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    s = load_settings(data_dir=Path("rel-data"))
    assert s.data_dir.is_absolute()
    assert s.data_dir == (tmp_path / "rel-data").resolve()
