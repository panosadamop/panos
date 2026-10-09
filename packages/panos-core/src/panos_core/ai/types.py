"""Provider-neutral request/response types (NFR-MNT-004: no vendor shapes in domain code).

Every message carries a **privacy label** and a **trust** marker:

* ``privacy`` — LOCAL_ONLY content must never reach a cloud provider (ADR-008);
* ``trust`` — UNTRUSTED content (files, tool output, retrieval, web, email) is data, never
  instructions (ADR-023). The context builder wraps it in labelled envelopes.
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from panos_core.domain.enums import PrivacyMode


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Trust(StrEnum):
    TRUSTED = "trusted"  # system prompt, the user's own words
    UNTRUSTED = "untrusted"  # anything read from the world


class Locality(StrEnum):
    LOCAL = "local"
    CLOUD = "cloud"


class TaskType(StrEnum):
    INTENT = "intent"  # classify a request
    CHAT = "chat"  # general conversation / Q&A
    SUMMARIZE = "summarize"
    CODING = "coding"
    REQUIREMENTS = "requirements"
    ARCHITECTURE = "architecture"
    REVIEW = "review"
    EMBED = "embed"
    VISION = "vision"


class Complexity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any]


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Role
    content: str = ""
    privacy: PrivacyMode = PrivacyMode.LOCAL_ONLY  # fail-safe default
    trust: Trust = Trust.TRUSTED
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    source: str | None = None  # provenance (file path, URL...) for untrusted content


class ToolDef(BaseModel):
    """A tool offered to the model (JSON Schema parameters)."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    parameters: dict[str, Any]


class ChatRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    messages: tuple[Message, ...]
    tools: tuple[ToolDef, ...] = ()
    task: TaskType = TaskType.CHAT
    complexity: Complexity = Complexity.MEDIUM
    max_output_tokens: int = Field(default=4096, gt=0)
    # Set by the gateway after secret redaction; cloud adapters refuse unredacted requests.
    redacted: bool = False

    @property
    def privacy(self) -> PrivacyMode:
        """The request's label: the most restrictive label of any message."""
        if any(m.privacy == PrivacyMode.LOCAL_ONLY for m in self.messages):
            return PrivacyMode.LOCAL_ONLY
        return PrivacyMode.STANDARD


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0


class ChatResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = Usage()
    model: str
    provider: str
    stop_reason: str | None = None


class StreamEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["text", "tool_call", "done"]
    text: str = ""
    tool_call: ToolCall | None = None
    response: ChatResponse | None = None  # on "done"
