"""Provider ports (ADR-009). Adapters implement these; nothing else knows a vendor API."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from panos_core.ai.types import ChatRequest, ChatResponse, Locality, StreamEvent
from panos_core.domain.enums import PrivacyMode


class ProviderError(RuntimeError):
    """A provider call failed. ``retryable`` marks transient failures (rate limit, 5xx, network)."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ProviderUnavailableError(ProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=True)


@dataclass(frozen=True)
class ProviderHealth:
    ok: bool
    detail: str = ""
    models: tuple[str, ...] = ()  # models the provider reports as available
    measured: dict[str, tuple[float, float]] = field(default_factory=dict)


class ChatProvider(Protocol):
    id: str
    kind: str
    locality: Locality

    async def chat(self, request: ChatRequest, model: str) -> ChatResponse: ...

    def stream(self, request: ChatRequest, model: str) -> AsyncIterator[StreamEvent]: ...

    async def embed(
        self, texts: Sequence[str], model: str, labels: Sequence[PrivacyMode]
    ) -> list[list[float]]: ...

    async def health(self) -> ProviderHealth: ...

    async def close(self) -> None: ...
