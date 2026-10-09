"""Build configured providers (composition root for AI adapters)."""

from typing import Any

from panos_core.ai.adapters.anthropic import AnthropicProvider
from panos_core.ai.adapters.mock import MockProvider
from panos_core.ai.adapters.ollama import OllamaProvider, SpeedCallback
from panos_core.ai.adapters.openai_compat import OpenAICompatibleProvider
from panos_core.ai.ports import ChatProvider
from panos_core.ai.privacy import endpoint_locality
from panos_core.ai.registry import ModelRegistry
from panos_core.ai.types import Locality
from panos_core.config.settings import AISettings, ProviderSettings
from panos_core.secrets import SecretStore

OPENAI_DEFAULT_URL = "https://api.openai.com"


LOCAL_TIMEOUT_S = 1800.0
CLOUD_TIMEOUT_S = 300.0


def _timeout(cfg: ProviderSettings) -> float:
    if cfg.timeout_s is not None:
        return cfg.timeout_s
    return LOCAL_TIMEOUT_S if cfg.kind == "ollama" else CLOUD_TIMEOUT_S


def api_key_name(provider_id: str) -> str:
    return f"ai.{provider_id}.api_key"


def build_providers(
    settings: AISettings,
    registry: ModelRegistry,
    secrets: SecretStore,
    *,
    on_speed: dict[str, SpeedCallback] | None = None,
) -> dict[str, ChatProvider]:
    providers: dict[str, Any] = {}
    for cfg in settings.providers:
        if not cfg.enabled:
            continue
        key = secrets.get(api_key_name(cfg.id))
        if cfg.kind == "ollama":
            url = cfg.base_url or "http://127.0.0.1:11434"
            providers[cfg.id] = OllamaProvider(
                cfg.id,
                url,
                locality=endpoint_locality(url, trusted_lan=cfg.trusted_lan),
                registry=registry,
                timeout_s=_timeout(cfg),
                on_speed=(on_speed or {}).get(cfg.id),
            )
        elif cfg.kind in ("openai_compatible", "openai"):
            url = cfg.base_url or OPENAI_DEFAULT_URL
            locality = (
                Locality.CLOUD
                if cfg.kind == "openai"
                else endpoint_locality(url, trusted_lan=cfg.trusted_lan)
            )
            providers[cfg.id] = OpenAICompatibleProvider(
                cfg.id,
                url,
                kind=cfg.kind,
                locality=locality,
                registry=registry,
                api_key=key,
                timeout_s=_timeout(cfg),
            )
        elif cfg.kind == "anthropic":
            providers[cfg.id] = AnthropicProvider(
                cfg.id,
                api_key=key,
                timeout_s=_timeout(cfg),
                refusal_fallback=settings.anthropic_refusal_fallback,
            )
        elif cfg.kind == "mock":
            providers[cfg.id] = MockProvider(id=cfg.id)
    return providers
