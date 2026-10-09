"""Ollama adapter (native API: /api/chat, /api/embed, /api/tags).

The native API is used rather than Ollama's OpenAI-compatible endpoint because it reports
per-call prompt/generation timings, which keep the router's speed estimates honest on
CPU-only machines (S-1).
"""

import json
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import httpx

from panos_core.ai.adapters.base import AdapterBase
from panos_core.ai.ports import ProviderError, ProviderHealth, ProviderUnavailableError
from panos_core.ai.registry import ModelRegistry, normalize_model_name
from panos_core.ai.toolcalls import parse_json_tool_calls
from panos_core.ai.types import (
    ChatRequest,
    ChatResponse,
    Locality,
    Role,
    StreamEvent,
    ToolCall,
    Usage,
)
from panos_core.domain.enums import PrivacyMode

SpeedCallback = Callable[[str, float, float], None]  # model, prompt_tps, gen_tps


def _to_ollama_messages(request: ChatRequest) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in request.messages:
        msg: dict[str, Any] = {"role": m.role.value, "content": m.content}
        if m.role == Role.ASSISTANT and m.tool_calls:
            msg["tool_calls"] = [
                {"function": {"name": c.name, "arguments": c.arguments}} for c in m.tool_calls
            ]
        out.append(msg)
    return out


def _tools(request: ChatRequest) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in request.tools
    ]


def _tps(count: Any, duration_ns: Any) -> float:
    try:
        return float(count) / (float(duration_ns) / 1e9) if count and duration_ns else 0.0
    except (TypeError, ZeroDivisionError):
        return 0.0


class OllamaProvider(AdapterBase):
    kind = "ollama"

    def __init__(
        self,
        provider_id: str,
        base_url: str,
        *,
        locality: Locality,
        registry: ModelRegistry,
        timeout_s: float = 300.0,
        on_speed: SpeedCallback | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.id = provider_id
        self.locality = locality
        self._registry = registry
        self._on_speed = on_speed
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            trust_env=False,
        )

    def _payload(self, request: ChatRequest, model: str, *, stream: bool) -> dict[str, Any]:
        info = self._registry.get(self.kind, model)
        num_ctx = info.context if info else 8192
        payload: dict[str, Any] = {
            "model": model,
            "messages": _to_ollama_messages(request),
            "stream": stream,
            "options": {"num_ctx": num_ctx, "num_predict": request.max_output_tokens},
            "keep_alive": "10m",
        }
        if request.tools:
            payload["tools"] = _tools(request)
        return payload

    def _response(
        self, request: ChatRequest, model: str, data: dict[str, Any], text: str
    ) -> ChatResponse:
        raw_calls = (data.get("message") or {}).get("tool_calls") or []
        calls = tuple(
            ToolCall(
                id=f"call_{uuid.uuid4().hex[:12]}",
                name=c["function"]["name"],
                arguments=c["function"].get("arguments") or {},
            )
            for c in raw_calls
            if isinstance(c, dict) and "function" in c
        )
        info = self._registry.get(self.kind, model)
        if not calls and request.tools and info is not None and info.tool_use == "json_in_content":
            calls = parse_json_tool_calls(text, (t.name for t in request.tools))
            if calls:
                text = ""
        if self._on_speed is not None:
            self._on_speed(
                model,
                _tps(data.get("prompt_eval_count"), data.get("prompt_eval_duration")),
                _tps(data.get("eval_count"), data.get("eval_duration")),
            )
        return ChatResponse(
            text=text,
            tool_calls=calls,
            model=model,
            provider=self.id,
            usage=Usage(
                input_tokens=int(data.get("prompt_eval_count") or 0),
                output_tokens=int(data.get("eval_count") or 0),
            ),
            stop_reason="tool_use" if calls else str(data.get("done_reason") or "stop"),
        )

    async def chat(self, request: ChatRequest, model: str) -> ChatResponse:
        self._guard(request)
        try:
            r = await self._client.post(
                "/api/chat", json=self._payload(request, model, stream=False)
            )
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(f"ollama unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise ProviderError(
                f"ollama error {r.status_code}: {r.text[:300]}", retryable=r.status_code >= 500
            )
        data = r.json()
        return self._response(request, model, data, (data.get("message") or {}).get("content", ""))

    async def stream(self, request: ChatRequest, model: str) -> AsyncIterator[StreamEvent]:
        self._guard(request)
        parts: list[str] = []
        last: dict[str, Any] = {}
        try:
            async with self._client.stream(
                "POST", "/api/chat", json=self._payload(request, model, stream=True)
            ) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", "replace")
                    raise ProviderError(f"ollama error {r.status_code}: {body[:300]}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    piece = (chunk.get("message") or {}).get("content", "")
                    if piece:
                        parts.append(piece)
                        yield StreamEvent(type="text", text=piece)
                    if chunk.get("done"):
                        last = chunk
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(f"ollama unreachable: {exc}") from exc
        response = self._response(request, model, last, "".join(parts))
        for call in response.tool_calls:
            yield StreamEvent(type="tool_call", tool_call=call)
        yield StreamEvent(type="done", response=response)

    async def embed(
        self, texts: Sequence[str], model: str, labels: Sequence[PrivacyMode]
    ) -> list[list[float]]:
        self._guard_texts(labels)
        try:
            r = await self._client.post(
                "/api/embed", json={"model": model, "input": list(texts), "keep_alive": "10m"}
            )
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(f"ollama unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise ProviderError(f"ollama embed error {r.status_code}: {r.text[:300]}")
        vectors: list[list[float]] = r.json().get("embeddings", [])
        if len(vectors) != len(texts):
            raise ProviderError("ollama returned an unexpected number of embeddings")
        return vectors

    async def health(self) -> ProviderHealth:
        try:
            r = await self._client.get("/api/tags", timeout=3.0)
            r.raise_for_status()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            return ProviderHealth(ok=False, detail=f"unreachable: {type(exc).__name__}")
        models = tuple(normalize_model_name(m["name"]) for m in r.json().get("models", []))
        return ProviderHealth(ok=True, models=models)

    async def close(self) -> None:
        await self._client.aclose()
