"""Mock AI providers and a matching model registry for tests (no network)."""

from dataclasses import dataclass, field

from panos_core.ai.adapters.mock import MockProvider, Responder
from panos_core.ai.registry import ModelInfo, ModelRegistry
from panos_core.ai.types import Locality

LOCAL_CHAT = "mock-local-chat"
LOCAL_EMBED = "mock-embed"
CLOUD_CHAT = "mock-cloud-chat"


def mock_models() -> ModelRegistry:
    return ModelRegistry(
        (
            ModelInfo(
                kind="mock",
                name=LOCAL_CHAT,
                tier="local_capable",
                context=32_000,
                tool_use="json_in_content",
                prompt_tps=500,
                gen_tps=50,
            ),
            ModelInfo(
                kind="mock",
                name=LOCAL_EMBED,
                tier="embedding",
                context=8192,
                embed_dim=8,
                query_prefix="q: ",
                doc_prefix="d: ",
            ),
            ModelInfo(
                kind="mock-cloud",
                name=CLOUD_CHAT,
                tier="cloud_efficient",
                context=200_000,
                tool_use="native",
                cost_in=2.0,
                cost_out=10.0,
                prompt_tps=2000,
                gen_tps=80,
            ),
        )
    )


@dataclass
class MockAI:
    local: MockProvider = field(
        default_factory=lambda: MockProvider(
            id="local", kind="mock", locality=Locality.LOCAL, models=(LOCAL_CHAT, LOCAL_EMBED)
        )
    )
    cloud: MockProvider = field(
        default_factory=lambda: MockProvider(
            id="cloud", kind="mock-cloud", locality=Locality.CLOUD, models=()
        )
    )
    models: ModelRegistry = field(default_factory=mock_models)

    @property
    def providers(self) -> dict[str, MockProvider]:
        return {"local": self.local, "cloud": self.cloud}

    def respond(self, responder: Responder) -> None:
        self.local.responder = responder
        self.cloud.responder = responder
