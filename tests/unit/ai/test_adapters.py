"""Provider adapters against mocked HTTP (respx) and a fake Anthropic SDK client."""

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from panos_core.ai.adapters.anthropic import (
    FALLBACK_BETA,
    AnthropicProvider,
    build_params,
    effort_for,
)
from panos_core.ai.adapters.factory import api_key_name, build_providers
from panos_core.ai.adapters.ollama import OllamaProvider
from panos_core.ai.adapters.openai_compat import OpenAICompatibleProvider
from panos_core.ai.ports import ProviderError, ProviderUnavailableError
from panos_core.ai.privacy import PrivacyViolationError
from panos_core.ai.registry import ModelRegistry
from panos_core.ai.types import (
    ChatRequest,
    Complexity,
    Locality,
    Message,
    Role,
    TaskType,
    ToolCall,
    ToolDef,
)
from panos_core.config.settings import AISettings, ProviderSettings
from panos_core.domain.enums import PrivacyMode
from panos_core.secrets import create_secret_store

pytestmark = pytest.mark.req("NFR-PORT-003", "NFR-MNT-004")

OLLAMA = "http://127.0.0.1:11434"
S, L = PrivacyMode.STANDARD, PrivacyMode.LOCAL_ONLY
TOOL = ToolDef(
    name="fs.read",
    description="read a file",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
)


def request(privacy: PrivacyMode = S, *, redacted: bool = False, **kw: Any) -> ChatRequest:
    return ChatRequest(
        messages=(
            Message(role=Role.SYSTEM, content="You are Panos.", privacy=S),
            Message(role=Role.USER, content="hi", privacy=privacy),
        ),
        redacted=redacted,
        **kw,
    )


# --------------------------------------------------------------------------- Ollama
@respx.mock
async def test_ollama_chat_usage_speed_and_num_ctx() -> None:
    speeds: list[tuple[str, float, float]] = []
    route = respx.post(f"{OLLAMA}/api/chat").respond(
        json={
            "message": {"role": "assistant", "content": "Γεια"},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 100,
            "prompt_eval_duration": 2_000_000_000,
            "eval_count": 20,
            "eval_duration": 4_000_000_000,
        }
    )
    provider = OllamaProvider(
        "ollama",
        OLLAMA,
        locality=Locality.LOCAL,
        registry=ModelRegistry(),
        on_speed=lambda *a: speeds.append(a),
    )
    response = await provider.chat(request(L), "qwen2.5:3b")  # LOCAL_ONLY is fine locally
    assert response.text == "Γεια" and response.usage.input_tokens == 100
    assert speeds == [("qwen2.5:3b", 50.0, 5.0)]
    body = json.loads(route.calls[0].request.content)
    assert body["options"]["num_ctx"] == 8192 and body["stream"] is False
    await provider.close()


@respx.mock
async def test_ollama_json_in_content_tool_calls_and_native_tool_calls() -> None:
    respx.post(f"{OLLAMA}/api/chat").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "message": {"content": '{"name": "fs.read", "arguments": {"path": "a"}}'},
                    "done": True,
                },
            ),
            httpx.Response(
                200,
                json={
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "fs.read", "arguments": {"path": "b"}}}
                        ],
                    },
                    "done": True,
                },
            ),
        ]
    )
    provider = OllamaProvider("ollama", OLLAMA, locality=Locality.LOCAL, registry=ModelRegistry())
    coder = await provider.chat(request(tools=(TOOL,)), "qwen2.5-coder:7b")
    assert [c.arguments for c in coder.tool_calls] == [{"path": "a"}] and coder.text == ""
    assert coder.stop_reason == "tool_use"
    native = await provider.chat(request(tools=(TOOL,)), "qwen2.5:3b")
    assert [c.arguments for c in native.tool_calls] == [{"path": "b"}]


@respx.mock
async def test_ollama_stream_embed_health_and_errors() -> None:
    lines = [
        {"message": {"content": "Hel"}},
        {"message": {"content": "lo"}},
        {"message": {"content": ""}, "done": True, "eval_count": 2},
    ]
    respx.post(f"{OLLAMA}/api/chat").respond(
        content="\n".join(json.dumps(x) for x in lines).encode()
    )
    respx.post(f"{OLLAMA}/api/embed").respond(json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})
    respx.get(f"{OLLAMA}/api/tags").respond(
        json={"models": [{"name": "qwen2.5:3b"}, {"name": "nomic-embed-text:latest"}]}
    )
    provider = OllamaProvider("ollama", OLLAMA, locality=Locality.LOCAL, registry=ModelRegistry())
    events = [e async for e in provider.stream(request(), "qwen2.5:3b")]
    assert "".join(e.text for e in events if e.type == "text") == "Hello"
    assert events[-1].type == "done" and events[-1].response is not None
    assert events[-1].response.text == "Hello"
    assert await provider.embed(["a", "b"], "nomic-embed-text", [L, L]) == [[0.1, 0.2], [0.3, 0.4]]
    health = await provider.health()
    assert health.ok and health.models == ("qwen2.5:3b", "nomic-embed-text")


@respx.mock
async def test_ollama_errors_map_to_provider_errors() -> None:
    respx.post(f"{OLLAMA}/api/chat").mock(
        side_effect=[
            httpx.ConnectError("refused"),
            httpx.Response(500, text="boom"),
            httpx.Response(404, text="model not found"),
        ]
    )
    respx.get(f"{OLLAMA}/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    provider = OllamaProvider("ollama", OLLAMA, locality=Locality.LOCAL, registry=ModelRegistry())
    with pytest.raises(ProviderUnavailableError):
        await provider.chat(request(), "qwen2.5:3b")
    with pytest.raises(ProviderError) as err:
        await provider.chat(request(), "qwen2.5:3b")
    assert err.value.retryable
    with pytest.raises(ProviderError) as err:
        await provider.chat(request(), "qwen2.5:3b")
    assert not err.value.retryable
    assert not (await provider.health()).ok


@pytest.mark.req("NFR-SEC-005", "AC-US005-03")
@respx.mock(assert_all_called=False)
async def test_remote_ollama_is_cloud_and_refuses_local_only(respx_mock: respx.Router) -> None:
    remote = "http://203.0.113.7:11434"
    route = respx_mock.post(f"{remote}/api/chat").respond(json={"message": {"content": "x"}})
    embed = respx_mock.post(f"{remote}/api/embed").respond(json={"embeddings": [[1.0]]})
    provider = OllamaProvider("gpu", remote, locality=Locality.CLOUD, registry=ModelRegistry())
    with pytest.raises(PrivacyViolationError):
        await provider.chat(request(L, redacted=True), "qwen2.5:3b")
    with pytest.raises(PrivacyViolationError):
        [e async for e in provider.stream(request(L, redacted=True), "qwen2.5:3b")]
    with pytest.raises(PrivacyViolationError):
        await provider.embed(["secret plan"], "nomic-embed-text", [L])
    assert not route.called and not embed.called


# --------------------------------------------------------------------------- OpenAI-compatible
API = "https://api.openai.example"


@pytest.mark.req("NFR-SEC-005", "AC-US005-03")
@respx.mock(assert_all_called=False)
async def test_openai_compatible_cloud_guard_blocks_before_any_request(
    respx_mock: respx.Router,
) -> None:
    route = respx_mock.post(f"{API}/v1/chat/completions").respond(json={})
    provider = OpenAICompatibleProvider(
        "openai",
        API,
        kind="openai",
        locality=Locality.CLOUD,
        registry=ModelRegistry(),
        api_key="k" * 20,
    )
    with pytest.raises(PrivacyViolationError):
        await provider.chat(request(L, redacted=True), "gpt-x")
    with pytest.raises(PrivacyViolationError):  # STANDARD but not redacted by the gateway
        await provider.chat(request(S), "gpt-x")
    assert not route.called


@respx.mock
async def test_openai_compatible_chat_and_stream() -> None:
    respx.post(f"{API}/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "type": "function",
                                        "function": {
                                            "name": "fs.read",
                                            "arguments": '{"path": "x"}',
                                        },
                                    },
                                    {
                                        "id": "c2",
                                        "type": "function",
                                        "function": {"name": "fs.read", "arguments": "{broken"},
                                    },
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                },
            ),
            httpx.Response(
                200,
                content=(
                    b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
                    b'data: {"choices":[{"delta":{"content":" there"},"finish_reason":"stop"}]}\n\n'
                    b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
                    b"data: [DONE]\n\n"
                ),
            ),
            httpx.Response(429, text="slow down"),
        ]
    )
    provider = OpenAICompatibleProvider(
        "openai",
        API,
        kind="openai",
        locality=Locality.CLOUD,
        registry=ModelRegistry(),
        api_key="k" * 20,
    )
    ok = request(S, redacted=True, tools=(TOOL,))
    response = await provider.chat(ok, "gpt-x")
    assert [c.id for c in response.tool_calls] == ["c1"]  # malformed call dropped
    assert response.usage.input_tokens == 12
    events = [e async for e in provider.stream(request(S, redacted=True), "gpt-x")]
    done = events[-1].response
    assert done is not None and done.text == "Hi there" and done.usage.output_tokens == 2
    with pytest.raises(ProviderError) as err:
        await provider.chat(request(S, redacted=True), "gpt-x")
    assert err.value.retryable


# --------------------------------------------------------------------------- Anthropic
@dataclass
class FakeStream:
    events: list[Any]
    final: Any

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __aiter__(self) -> "FakeStream":
        self._it = iter(self.events)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None

    async def get_final_message(self) -> Any:
        return self.final


@dataclass
class FakeMessages:
    reply: Any
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, **params: Any) -> Any:
        self.calls.append(params)
        return self.reply

    def stream(self, **params: Any) -> FakeStream:
        self.calls.append(params)
        return FakeStream(
            [SimpleNamespace(type="text", text="He"), SimpleNamespace(type="text", text="llo")],
            self.reply,
        )


def fake_message(text: str = "Hello", stop: str = "end_turn") -> Any:
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="..."),
            SimpleNamespace(type="text", text=text),
            SimpleNamespace(type="tool_use", id="tu_1", name="fs.read", input={"path": "a"}),
        ],
        model="claude-sonnet-5-5",
        stop_reason=stop,
        usage=SimpleNamespace(input_tokens=30, output_tokens=7),
    )


def fake_client() -> Any:
    reply = fake_message()
    return SimpleNamespace(
        messages=FakeMessages(reply),
        beta=SimpleNamespace(messages=FakeMessages(reply)),
        models=SimpleNamespace(),
    )


def test_build_params_shapes_request_for_current_models() -> None:
    req = ChatRequest(
        messages=(
            Message(role=Role.SYSTEM, content="A", privacy=S),
            Message(role=Role.SYSTEM, content="B", privacy=S),
            Message(role=Role.USER, content="read a", privacy=S),
            Message(
                role=Role.ASSISTANT,
                content="ok",
                privacy=S,
                tool_calls=(ToolCall(id="tu_1", name="fs.read", arguments={"path": "a"}),),
            ),
            Message(role=Role.TOOL, content="file body", privacy=S, tool_call_id="tu_1"),
        ),
        tools=(TOOL,),
        task=TaskType.REVIEW,
        redacted=True,
    )
    params = build_params(req, "claude-sonnet-5-5", fallback=True)
    assert params["system"] == "A\n\nB"
    assert [m["role"] for m in params["messages"]] == ["user", "assistant", "user"]
    assert params["messages"][1]["content"][1]["type"] == "tool_use"
    assert params["messages"][2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "tu_1",
        "content": "file body",
    }
    assert params["tools"][0]["input_schema"]["type"] == "object"
    assert params["output_config"] == {"effort": "high"}
    assert params["betas"] == [FALLBACK_BETA] and params["fallbacks"] == "default"
    assert "temperature" not in params and "tool_choice" not in params
    haiku = build_params(req, "claude-haiku-4-5", fallback=True)
    assert "output_config" not in haiku and "betas" not in haiku
    assert "betas" not in build_params(req, "claude-opus-5-5", fallback=False)
    assert effort_for(TaskType.INTENT, Complexity.HIGH) == "low"
    assert effort_for(TaskType.CHAT, Complexity.MEDIUM) == "medium"


async def test_anthropic_chat_stream_use_beta_surface_only_with_fallback() -> None:
    client = fake_client()
    provider = AnthropicProvider("anthropic", api_key=None, client=client)
    response = await provider.chat(request(S, redacted=True), "claude-sonnet-5-5")
    assert response.text == "Hello" and response.tool_calls[0].name == "fs.read"
    assert response.usage.input_tokens == 30
    assert len(client.beta.messages.calls) == 1 and not client.messages.calls
    events = [e async for e in provider.stream(request(S, redacted=True), "claude-haiku-4-5")]
    assert "".join(e.text for e in events if e.type == "text") == "Hello"
    assert [e.type for e in events][-2:] == ["tool_call", "done"]
    assert len(client.messages.calls) == 1  # haiku: no fallback beta


@pytest.mark.req("NFR-SEC-005", "AC-US005-03")
async def test_anthropic_guard_runs_before_the_sdk_is_touched() -> None:
    client = fake_client()
    provider = AnthropicProvider("anthropic", api_key=None, client=client)
    with pytest.raises(PrivacyViolationError):
        await provider.chat(request(L, redacted=True), "claude-sonnet-5-5")
    with pytest.raises(PrivacyViolationError):
        [e async for e in provider.stream(request(S), "claude-sonnet-5-5")]
    with pytest.raises(PrivacyViolationError):
        await provider.embed(["x"], "any", [L])
    assert not client.messages.calls and not client.beta.messages.calls
    no_key = AnthropicProvider("anthropic", api_key=None)
    with pytest.raises(PrivacyViolationError):  # guard first, even without a key
        await no_key.chat(request(L, redacted=True), "claude-sonnet-5-5")
    with pytest.raises(ProviderError, match="not configured"):
        await no_key.chat(request(S, redacted=True), "claude-sonnet-5-5")
    assert not (await no_key.health()).ok


async def test_anthropic_refusal_is_surfaced() -> None:
    client = fake_client()
    client.beta.messages.reply = fake_message("", stop="refusal")
    provider = AnthropicProvider("anthropic", api_key=None, client=client)
    response = await provider.chat(request(S, redacted=True), "claude-opus-5-5")
    assert response.stop_reason == "refusal"


def test_factory_builds_enabled_providers_with_keyring_keys() -> None:
    secrets = create_secret_store("memory")
    secrets.set(api_key_name("lan"), "k" * 20)
    settings = AISettings(
        providers=[
            ProviderSettings(id="ollama", kind="ollama", base_url=OLLAMA),
            ProviderSettings(
                id="lan", kind="openai_compatible", base_url="http://192.168.1.5:8080"
            ),
            ProviderSettings(
                id="lan2",
                kind="openai_compatible",
                base_url="http://192.168.1.6:8080",
                trusted_lan=True,
            ),
            ProviderSettings(id="anthropic", kind="anthropic"),
            ProviderSettings(id="off", kind="ollama", enabled=False),
            ProviderSettings(id="mock", kind="mock"),
        ]
    )
    providers = build_providers(settings, ModelRegistry(), secrets)
    assert set(providers) == {"ollama", "lan", "lan2", "anthropic", "mock"}
    assert providers["ollama"].locality == Locality.LOCAL
    assert providers["lan"].locality == Locality.CLOUD  # untrusted LAN is treated as cloud
    assert providers["lan2"].locality == Locality.LOCAL
    assert providers["anthropic"].locality == Locality.CLOUD
