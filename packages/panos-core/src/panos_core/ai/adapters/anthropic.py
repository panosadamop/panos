"""Anthropic adapter (official ``anthropic`` SDK, Messages API).

* The Panos egress guard runs before every SDK call (LOCAL_ONLY or unredacted → refused).
* Current models reject sampling parameters and forced ``tool_choice``: neither is sent.
  Thinking is adaptive by default; depth is steered with ``output_config.effort``.
* ``stop_reason == "refusal"`` is surfaced, and, when enabled, the server-side refusal
  fallback (``fallbacks: "default"``, beta ``server-side-fallback-2026-07-01``) is
  requested for the models that support it.
* The API key comes from the OS keyring (never the environment), so cloud use is always
  an explicit opt-in.
"""

from collections.abc import AsyncIterator, Sequence
from typing import Any, Final

from panos_core.ai.adapters.base import AdapterBase
from panos_core.ai.ports import ProviderError, ProviderHealth, ProviderUnavailableError
from panos_core.ai.types import (
    ChatRequest,
    ChatResponse,
    Complexity,
    Locality,
    Role,
    StreamEvent,
    TaskType,
    ToolCall,
    Usage,
)
from panos_core.domain.enums import PrivacyMode

FALLBACK_BETA: Final = "server-side-fallback-2026-07-01"
# Models accepting the scalar "default" fallback form on the Claude API.
FALLBACK_MODELS: Final = frozenset({"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"})
# Haiku 4.5 rejects the effort parameter.
NO_EFFORT_MODELS: Final = frozenset({"claude-haiku-4-5"})


def effort_for(task: TaskType, complexity: Complexity) -> str:
    if task in (TaskType.INTENT, TaskType.SUMMARIZE) or complexity == Complexity.LOW:
        return "low"
    if complexity == Complexity.HIGH or task in (TaskType.ARCHITECTURE, TaskType.REVIEW):
        return "high"
    return "medium"


def build_params(request: ChatRequest, model: str, *, fallback: bool) -> dict[str, Any]:
    system = "\n\n".join(m.content for m in request.messages if m.role == Role.SYSTEM)
    messages: list[dict[str, Any]] = []
    for m in request.messages:
        if m.role == Role.SYSTEM:
            continue
        if m.role == Role.TOOL:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id or "",
                            "content": m.content,
                        }
                    ],
                }
            )
        elif m.role == Role.ASSISTANT and m.tool_calls:
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            blocks += [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in m.tool_calls
            ]
            messages.append({"role": "assistant", "content": blocks})
        elif m.images:
            blocks = [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img},
                }
                for img in m.images
            ]
            messages.append(
                {"role": m.role.value, "content": [*blocks, {"type": "text", "text": m.content}]}
            )
        else:
            messages.append({"role": m.role.value, "content": m.content})
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": request.max_output_tokens,
        "messages": messages,
    }
    if system:
        params["system"] = system
    if request.tools:
        params["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in request.tools
        ]
    if model not in NO_EFFORT_MODELS:
        params["output_config"] = {"effort": effort_for(request.task, request.complexity)}
    if fallback and model in FALLBACK_MODELS:
        params["betas"] = [FALLBACK_BETA]
        params["fallbacks"] = "default"
    return params


def parse_message(message: Any, provider_id: str) -> ChatResponse:
    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in message.content:
        if block.type == "text":
            texts.append(block.text)
        elif block.type == "tool_use":
            calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        # thinking / fallback / server-tool blocks are not surfaced to the conversation
    usage = getattr(message, "usage", None)
    return ChatResponse(
        text="".join(texts),
        tool_calls=tuple(calls),
        model=getattr(message, "model", ""),
        provider=provider_id,
        stop_reason=getattr(message, "stop_reason", None),
        usage=Usage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        ),
    )


class AnthropicProvider(AdapterBase):
    kind = "anthropic"
    locality = Locality.CLOUD

    def __init__(
        self,
        provider_id: str,
        *,
        api_key: str | None,
        timeout_s: float = 300.0,
        refusal_fallback: bool = True,
        client: Any = None,
    ) -> None:
        self.id = provider_id
        self._fallback = refusal_fallback
        self._has_key = bool(api_key) or client is not None
        if client is None and api_key:
            import anthropic  # optional dependency: only needed when Claude is configured

            client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_s, max_retries=2)
        self._client = client

    def _api(self, params: dict[str, Any]) -> Any:
        """Beta surface only when a beta (refusal fallback) is requested."""
        return self._client.beta.messages if "betas" in params else self._client.messages

    def _require_client(self) -> None:
        if self._client is None:
            raise ProviderError("Anthropic API key is not configured (panos ai set-key anthropic)")

    @staticmethod
    def _translate(exc: Exception) -> ProviderError:
        import anthropic

        if isinstance(exc, anthropic.RateLimitError | anthropic.APIConnectionError):
            return ProviderUnavailableError(f"anthropic unavailable: {type(exc).__name__}")
        if isinstance(exc, anthropic.APIStatusError):
            return ProviderError(
                f"anthropic error {exc.status_code}: {exc.message[:300]}",
                retryable=exc.status_code >= 500,
            )
        return ProviderError(f"anthropic error: {type(exc).__name__}")

    async def chat(self, request: ChatRequest, model: str) -> ChatResponse:
        self._guard(request)  # before anything else, including client checks
        self._require_client()
        params = build_params(request, model, fallback=self._fallback)
        try:
            message = await self._api(params).create(**params)
        except Exception as exc:
            raise self._translate(exc) from exc
        return parse_message(message, self.id)

    async def stream(self, request: ChatRequest, model: str) -> AsyncIterator[StreamEvent]:
        self._guard(request)
        self._require_client()
        params = build_params(request, model, fallback=self._fallback)
        try:
            async with self._api(params).stream(**params) as stream:
                async for event in stream:
                    if event.type == "text" and event.text:
                        yield StreamEvent(type="text", text=event.text)
                message = await stream.get_final_message()
        except ProviderError:
            raise
        except Exception as exc:
            raise self._translate(exc) from exc
        response = parse_message(message, self.id)
        for call in response.tool_calls:
            yield StreamEvent(type="tool_call", tool_call=call)
        yield StreamEvent(type="done", response=response)

    async def embed(
        self, texts: Sequence[str], model: str, labels: Sequence[PrivacyMode]
    ) -> list[list[float]]:
        self._guard_texts(labels)
        raise ProviderError("Anthropic does not provide an embeddings endpoint")

    async def health(self) -> ProviderHealth:
        if self._client is None:
            return ProviderHealth(ok=False, detail="no API key configured")
        try:
            page = await self._client.models.list(limit=50)
        except Exception as exc:
            return ProviderHealth(ok=False, detail=str(self._translate(exc)))
        return ProviderHealth(ok=True, models=tuple(m.id for m in page.data))

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            await self._client.close()
