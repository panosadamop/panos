"""Layered settings (M1-02).

Precedence, highest first:
1. explicit keyword overrides passed to :func:`load_settings`;
2. environment variables ``PANOS_*`` (nested keys use ``__``);
3. the user config file (TOML) — ``PANOS_CONFIG_FILE`` or ``<config_dir>/config.toml``;
4. built-in defaults (per-user platform directories, ``~/Panos/Projects`` workspace).

Secrets never live here (ADR-019); settings hold references only.
"""

import os
from pathlib import Path
from typing import Any, Literal

import platformdirs
from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

APP_NAME = "Panos"
CONFIG_FILE_ENV = "PANOS_CONFIG_FILE"

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

# Origins the Tauri WebView uses (macOS/Linux and Windows respectively).
DEFAULT_ALLOWED_ORIGINS = ("tauri://localhost", "http://tauri.localhost")


def _default_data_dir() -> Path:
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))


def _default_config_dir() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))


def _default_log_dir() -> Path:
    return Path(platformdirs.user_log_dir(APP_NAME, appauthor=False))


def _default_runtime_dir() -> Path:
    return Path(platformdirs.user_runtime_dir(APP_NAME, appauthor=False))


def _default_workspace_dir() -> Path:
    return Path.home() / APP_NAME / "Projects"


def default_config_file() -> Path:
    override = os.environ.get(CONFIG_FILE_ENV)
    return Path(override) if override else _default_config_dir() / "config.toml"


class PanosSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PANOS_",
        env_nested_delimiter="__",
        extra="forbid",
        validate_default=True,
    )

    data_dir: Path = Field(default_factory=_default_data_dir)
    config_dir: Path = Field(default_factory=_default_config_dir)
    log_dir: Path = Field(default_factory=_default_log_dir)
    runtime_dir: Path = Field(default_factory=_default_runtime_dir)
    workspace_dir: Path = Field(default_factory=_default_workspace_dir)

    # "memory" exists for tests only: secrets (incl. the approval-signing key) vanish on exit.
    secret_backend: Literal["keyring", "memory"] = "keyring"  # noqa: S105 - not a secret

    log_level: LogLevel = "INFO"
    log_json_console: bool = False
    allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS

    @field_validator("data_dir", "config_dir", "log_dir", "runtime_dir", "workspace_dir")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @property
    def database_path(self) -> Path:
        return self.data_dir / "panos.db"

    @property
    def runtime_file(self) -> Path:
        return self.runtime_dir / "node.json"

    @property
    def lock_file(self) -> Path:
        return self.runtime_dir / "node.lock"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        toml = TomlConfigSettingsSource(settings_cls, toml_file=default_config_file())
        return (init_settings, env_settings, toml)


def load_settings(**overrides: Any) -> PanosSettings:
    """Load settings using the documented precedence."""
    return PanosSettings(**overrides)


def ensure_directories(settings: PanosSettings) -> None:
    """Create per-user directories and the default workspace (BR-029)."""
    for directory in (
        settings.data_dir,
        settings.config_dir,
        settings.log_dir,
        settings.runtime_dir,
        settings.workspace_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
