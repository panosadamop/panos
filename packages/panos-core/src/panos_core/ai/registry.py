"""Model capability registry (ADR-009): static metadata + runtime-measured speeds."""

from functools import cache
from importlib import resources
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Tier = Literal[
    "local_light",
    "local_capable",
    "local_large",
    "cloud_light",
    "cloud_efficient",
    "cloud_premium",
    "embedding",
]
ToolUse = Literal["native", "json_in_content", "none"]


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    name: str
    tier: Tier
    context: int = Field(gt=0)
    max_output: int = 4096
    tool_use: ToolUse = "none"
    vision: bool = False
    languages: list[str] = Field(default_factory=lambda: ["en"])
    cost_in: float = 0.0  # USD per 1M input tokens
    cost_out: float = 0.0
    prompt_tps: float = 100.0
    gen_tps: float = 10.0
    embed_dim: int | None = None
    query_prefix: str = ""
    doc_prefix: str = ""

    @property
    def is_embedding(self) -> bool:
        return self.tier == "embedding"

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.cost_in + output_tokens * self.cost_out) / 1_000_000

    def estimate_latency(self, input_tokens: int, output_tokens: int) -> float:
        return input_tokens / max(self.prompt_tps, 0.1) + output_tokens / max(self.gen_tps, 0.1)


def normalize_model_name(name: str) -> str:
    """Ollama reports ``name:latest``; the registry uses the bare name."""
    return name[: -len(":latest")] if name.endswith(":latest") else name


@cache
def _default_models() -> tuple[ModelInfo, ...]:
    text = resources.files("panos_core.ai").joinpath("defaults/models.yaml").read_text("utf-8")
    data: dict[str, Any] = yaml.safe_load(text)
    return tuple(ModelInfo.model_validate(m) for m in data["models"])


class ModelRegistry:
    def __init__(self, models: tuple[ModelInfo, ...] | None = None) -> None:
        source = models if models is not None else _default_models()
        self._models = [m.model_copy() for m in source]  # runtime speed updates stay local

    def for_kind(self, kind: str) -> list[ModelInfo]:
        return [m for m in self._models if m.kind == kind]

    def get(self, kind: str, name: str) -> ModelInfo | None:
        target = normalize_model_name(name)
        return next((m for m in self._models if m.kind == kind and m.name == target), None)

    def update_speed(self, kind: str, name: str, prompt_tps: float, gen_tps: float) -> None:
        """Exponential moving average of measured throughput (NFR-PERF, S-1 follow-up)."""
        model = self.get(kind, name)
        if model is None:
            return
        if prompt_tps > 0:
            model.prompt_tps = round(0.7 * model.prompt_tps + 0.3 * prompt_tps, 2)
        if gen_tps > 0:
            model.gen_tps = round(0.7 * model.gen_tps + 0.3 * gen_tps, 2)

    def all(self) -> list[ModelInfo]:
        return list(self._models)
