"""Fallback parsing for models that emit tool calls as JSON text (``tool_use: json_in_content``).

S-1 showed qwen2.5-coder choosing the right tool but returning the call as JSON in the
message content instead of structured ``tool_calls``. The parser is strict: it only
accepts an object (optionally inside a code fence) naming a *declared* tool with object
arguments; anything else stays plain text.
"""

import json
import re
import uuid
from collections.abc import Iterable
from typing import Any

from panos_core.ai.types import ToolCall

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def parse_json_tool_calls(content: str, declared: Iterable[str]) -> tuple[ToolCall, ...]:
    names = set(declared)
    if not names:
        return ()
    text = content.strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith(("{", "[")):
        return ()
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        return ()
    items = data if isinstance(data, list) else [data]
    calls: list[ToolCall] = []
    for item in items:
        if not isinstance(item, dict):
            return ()
        name = item.get("name")
        args = item.get("arguments", item.get("parameters", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return ()
        if name not in names or not isinstance(args, dict):
            return ()
        calls.append(ToolCall(id=f"call_{uuid.uuid4().hex[:12]}", name=name, arguments=args))
    return tuple(calls)
