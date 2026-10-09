"""Mock AI providers, a matching model registry and scripted agents (no network)."""

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from panos_core.ai.adapters.mock import MockProvider, Responder
from panos_core.ai.registry import ModelInfo, ModelRegistry
from panos_core.ai.types import ChatRequest, Locality, Role

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


_ROLE_RE = re.compile(r"You are the (\w+) agent")

MULTIPLY_PLAN = {
    "summary": "Add multiply() to calc.py with a unit test",
    "steps": [
        {"description": "Implement multiply", "files": ["calc.py"]},
        {"description": "Test multiply", "files": ["tests/test_calc.py"]},
    ],
    "files_to_read": ["calc.py", "tests/test_calc.py"],
    "decisions": ["keep the module-level function style"],
    "assumptions": ["integers only"],
}


def multiply_change(body: str = "a * b") -> dict[str, Any]:
    return {
        "summary": "Add multiply",
        "commit_message": "feat(calc): add multiply",
        "edits": [
            {
                "path": "calc.py",
                "action": "edit",
                "search": "    return a + b\n",
                "replace": f"    return a + b\n\n\ndef multiply(a: int, b: int) -> int:\n"
                f"    return {body}\n",
            },
            {
                "path": "tests/test_multiply.py",
                "action": "create",
                "content": "import unittest\n\nfrom calc import multiply\n\n\n"
                "class MultiplyTest(unittest.TestCase):\n"
                "    def test_multiply(self) -> None:\n"
                "        self.assertEqual(multiply(3, 4), 12)\n",
            },
        ],
    }


FIX_MULTIPLY = {
    "summary": "Fix multiply",
    "edits": [
        {
            "path": "calc.py",
            "action": "edit",
            "search": "    return a + b + 1\n",
            "replace": "    return a * b\n",
        }
    ],
}
APPROVE = {"verdict": "approve", "summary": "Looks correct and tested.", "findings": []}
DIAGNOSIS = {
    "summary": "multiply returns the wrong value",
    "likely_cause": "wrong operator",
    "files": ["calc.py"],
}


class ScriptedAgents:
    """A responder that answers per agent role from queues (falls back to defaults)."""

    def __init__(self, **scripts: list[Any]) -> None:
        self.queues: dict[str, list[Any]] = defaultdict(list)
        for role, items in scripts.items():
            self.queues[role.lower()] = list(items)
        self.defaults: dict[str, Any] = {
            "planner": MULTIPLY_PLAN,
            "developer": multiply_change(),
            "reviewer": APPROVE,
            "qa": DIAGNOSIS,
        }
        self.calls: list[tuple[str, ChatRequest]] = []

    def __call__(self, request: ChatRequest, model: str) -> str:
        system = next((m.content for m in request.messages if m.role == Role.SYSTEM), "")
        match = _ROLE_RE.search(system)
        role = match.group(1).lower() if match else "chat"
        self.calls.append((role, request))
        queue = self.queues[role]
        item = queue.pop(0) if queue else self.defaults.get(role, "ok")
        return item if isinstance(item, str) else json.dumps(item)

    def roles(self) -> list[str]:
        return [role for role, _ in self.calls]
