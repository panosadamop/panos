"""Scripted provider for tests and offline development. Records every request it receives."""

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field

from panos_core.ai.adapters.base import AdapterBase
from panos_core.ai.ports import ProviderHealth, ProviderUnavailableError
from panos_core.ai.types import ChatRequest, ChatResponse, Locality, StreamEvent, Usage
from panos_core.domain.enums import PrivacyMode

Responder = Callable[[ChatRequest, str], ChatResponse | str]


@dataclass
class MockProvider(AdapterBase):
    id: str = "mock"
    kind: str = "mock"
    locality: Locality = Locality.LOCAL
    models: tuple[str, ...] = ("mock-model",)
    responder: Responder | None = None
    available: bool = True
    calls: list[tuple[ChatRequest, str]] = field(default_factory=list)
    embed_calls: list[tuple[list[str], str]] = field(default_factory=list)
    dim: int = 8

    def _respond(self, request: ChatRequest, model: str) -> ChatResponse:
        if not self.available:
            raise ProviderUnavailableError(f"{self.id} offline")
        self.calls.append((request, model))
        result = self.responder(request, model) if self.responder else f"[{self.id}] ok"
        if isinstance(result, ChatResponse):
            return result
        tokens_in = sum(len(m.content) for m in request.messages) // 4
        return ChatResponse(
            text=result,
            model=model,
            provider=self.id,
            stop_reason="end_turn",
            usage=Usage(input_tokens=tokens_in, output_tokens=len(result) // 4),
        )

    async def chat(self, request: ChatRequest, model: str) -> ChatResponse:
        self._guard(request)
        return self._respond(request, model)

    async def stream(self, request: ChatRequest, model: str) -> AsyncIterator[StreamEvent]:
        self._guard(request)
        response = self._respond(request, model)
        for word in response.text.split(" "):
            yield StreamEvent(type="text", text=word + " ")
        yield StreamEvent(type="done", response=response)

    async def embed(
        self, texts: Sequence[str], model: str, labels: Sequence[PrivacyMode]
    ) -> list[list[float]]:
        self._guard_texts(labels)
        if not self.available:
            raise ProviderUnavailableError(f"{self.id} offline")
        self.embed_calls.append((list(texts), model))
        vectors = []
        for text in texts:  # deterministic bag-of-characters vectors
            vec = [0.0] * self.dim
            for i, ch in enumerate(text.lower()):
                vec[(ord(ch) + i % 3) % self.dim] += 1.0
            vectors.append(vec)
        return vectors

    async def health(self) -> ProviderHealth:
        return ProviderHealth(ok=self.available, models=self.models if self.available else ())

    async def close(self) -> None:
        return None
