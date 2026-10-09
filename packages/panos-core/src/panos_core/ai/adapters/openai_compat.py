"""OpenAI-compatible adapter (/v1/chat/completions, /v1/embeddings, /v1/models).

Covers llama.cpp server, LM Studio, vLLM and OpenAI itself. Locality comes from the
endpoint (ADR-009): a loopback server is local; api.openai.com is cloud. No model IDs
or prices are hard-coded for OpenAI: they are configured, not assumed.
"""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from panos_core.ai.adapters.base import AdapterBase
from panos_core.ai.ports import ProviderError, ProviderHealth, ProviderUnavailableError
from panos_core.ai.registry import ModelRegistry
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


def _messages(request: ChatRequest) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in request.messages:
        msg: dict[str, Any] = {"role": m.role.value, "content": m.content}
        if m.role == Role.ASSISTANT and m.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for c in m.tool_calls
            ]
        if m.role == Role.TOOL:
            msg["tool_call_id"] = m.tool_call_id or ""
        out.append(msg)
    return out


def _parse_calls(raw: list[dict[str, Any]]) -> tuple[ToolCall, ...]:
    calls = []
    for c in raw:
        fn = c.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            continue  # malformed arguments are dropped, never executed
        if isinstance(args, dict) and fn.get("name"):
            calls.append(ToolCall(id=c.get("id") or "call", name=fn["name"], arguments=args))
    return tuple(calls)


class OpenAICompatibleProvider(AdapterBase):
    def __init__(
        self,
        provider_id: str,
        base_url: str,
        *,
        kind: str,
        locality: Locality,
        registry: ModelRegistry,
        api_key: str | None = None,
        timeout_s: float = 300.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.id = provider_id
        self.kind = kind
        self.locality = locality
        self._registry = registry
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout_s = timeout_s
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            trust_env=False,
        )

    def _payload(self, request: ChatRequest, model: str, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": _messages(request),
            "max_tokens": request.max_output_tokens,
            "stream": stream,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _finish(
        self,
        request: ChatRequest,
        model: str,
        text: str,
        calls: tuple[ToolCall, ...],
        usage: dict[str, Any],
        stop: str | None,
    ) -> ChatResponse:
        info = self._registry.get(self.kind, model)
        if not calls and request.tools and info is not None and info.tool_use == "json_in_content":
            calls = parse_json_tool_calls(text, (t.name for t in request.tools))
            if calls:
                text = ""
        return ChatResponse(
            text=text,
            tool_calls=calls,
            model=model,
            provider=self.id,
            stop_reason=stop,
            usage=Usage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
            ),
        )

    async def chat(self, request: ChatRequest, model: str) -> ChatResponse:
        self._guard(request)
        try:
            r = await self._client.post(
                "/v1/chat/completions", json=self._payload(request, model, stream=False)
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"{self.id} timed out after {self._timeout_s:g} s (generation too slow for "
                "this hardware; raise ai.providers[].timeout_s or use a faster model)",
                retryable=False,
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(f"{self.id} unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise ProviderError(
                f"{self.id} error {r.status_code}: {r.text[:300]}",
                retryable=r.status_code in (408, 429) or r.status_code >= 500,
            )
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return self._finish(
            request,
            model,
            message.get("content") or "",
            _parse_calls(message.get("tool_calls") or []),
            data.get("usage") or {},
            choice.get("finish_reason"),
        )

    async def stream(self, request: ChatRequest, model: str) -> AsyncIterator[StreamEvent]:
        self._guard(request)
        parts: list[str] = []
        calls_acc: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        stop: str | None = None
        try:
            async with self._client.stream(
                "POST", "/v1/chat/completions", json=self._payload(request, model, stream=True)
            ) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", "replace")
                    raise ProviderError(f"{self.id} error {r.status_code}: {body[:300]}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    chunk = json.loads(data_str)
                    usage = chunk.get("usage") or usage
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            parts.append(delta["content"])
                            yield StreamEvent(type="text", text=delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            acc = calls_acc.setdefault(
                                tc.get("index", 0),
                                {"id": "", "function": {"name": "", "arguments": ""}},
                            )
                            acc["id"] = tc.get("id") or acc["id"]
                            fn = tc.get("function") or {}
                            acc["function"]["name"] += fn.get("name") or ""
                            acc["function"]["arguments"] += fn.get("arguments") or ""
                        stop = choice.get("finish_reason") or stop
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"{self.id} timed out after {self._timeout_s:g} s (generation too slow for "
                "this hardware; raise ai.providers[].timeout_s or use a faster model)",
                retryable=False,
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(f"{self.id} unreachable: {exc}") from exc
        response = self._finish(
            request,
            model,
            "".join(parts),
            _parse_calls([calls_acc[i] for i in sorted(calls_acc)]),
            usage,
            stop,
        )
        for call in response.tool_calls:
            yield StreamEvent(type="tool_call", tool_call=call)
        yield StreamEvent(type="done", response=response)

    async def embed(
        self, texts: Sequence[str], model: str, labels: Sequence[PrivacyMode]
    ) -> list[list[float]]:
        self._guard_texts(labels)
        try:
            r = await self._client.post(
                "/v1/embeddings", json={"model": model, "input": list(texts)}
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"{self.id} timed out after {self._timeout_s:g} s (generation too slow for "
                "this hardware; raise ai.providers[].timeout_s or use a faster model)",
                retryable=False,
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(f"{self.id} unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise ProviderError(f"{self.id} embed error {r.status_code}: {r.text[:300]}")
        rows = sorted(r.json().get("data", []), key=lambda d: d.get("index", 0))
        return [list(map(float, row["embedding"])) for row in rows]

    async def health(self) -> ProviderHealth:
        try:
            r = await self._client.get("/v1/models", timeout=5.0)
            r.raise_for_status()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            return ProviderHealth(ok=False, detail=f"unreachable: {type(exc).__name__}")
        return ProviderHealth(
            ok=True, models=tuple(m.get("id", "") for m in r.json().get("data", []))
        )

    async def close(self) -> None:
        await self._client.aclose()
