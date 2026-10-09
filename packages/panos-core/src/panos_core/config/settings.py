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
from pydantic import BaseModel, ConfigDict, Field, field_validator
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


ProviderKind = Literal["ollama", "openai_compatible", "openai", "anthropic", "mock"]


class ProviderSettings(BaseModel):
    """One AI provider endpoint (ADR-009). API keys live in the OS keyring, never here."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_\-]{0,40}$")
    kind: ProviderKind
    base_url: str | None = None
    enabled: bool = True
    # A LAN endpoint counts as *local* only if the user explicitly trusts it (ADR-009).
    trusted_lan: bool = False
    timeout_s: float = Field(default=300.0, gt=0)


def _default_providers() -> list[ProviderSettings]:
    return [
        ProviderSettings(id="ollama", kind="ollama", base_url="http://127.0.0.1:11434"),
        ProviderSettings(id="anthropic", kind="anthropic", enabled=True),
    ]


class AISettings(BaseModel):
    """AI routing configuration (AI_ROUTING.md, ADR-008/009)."""

    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderSettings] = Field(default_factory=_default_providers)
    # Privacy for conversations not scoped to a project. Fail-safe default (ADR-024).
    unscoped_privacy: Literal["LOCAL_ONLY", "STANDARD"] = "LOCAL_ONLY"
    # Cost controls (FR-023, AI_ROUTING "Budget controls"); None = unlimited.
    max_cost_per_request_usd: float | None = Field(default=0.50, ge=0)
    monthly_budget_usd: float | None = Field(default=20.0, ge=0)
    allow_premium: bool = True
    # Anthropic server-side refusal fallback (recommended for Opus/Sonnet 5.5, Fable).
    anthropic_refusal_fallback: bool = True


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
    ai: AISettings = Field(default_factory=AISettings)

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
