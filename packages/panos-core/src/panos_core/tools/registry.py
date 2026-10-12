"""Allowlisted tool registry.

Only registered tools can run. Registration fails if a tool declares a capability that
is unknown to the policy or that must never exist in the runtime (email sending, BR-019;
browser submission and upload, BR-022).
"""

from collections.abc import Iterable
from typing import Any, Final

from panos_core.policy import Policy
from panos_core.tools.contract import Tool

# Capabilities that must not exist in the runtime at all, not merely be denied
# (AC-US043-02, AC-US045-02). Browser submission/upload stay P4 in policy but have no tool
# until a reviewed, user-driven flow exists (ADR-027).
FORBIDDEN_CAPABILITIES: Final = frozenset({"email.send", "browser.submit", "browser.upload"})


class ToolRegistrationError(ValueError):
    pass


class ToolRegistry:
    def __init__(self, policy: Policy, tools: Iterable[Tool[Any, Any]] = ()) -> None:
        self._policy = policy
        self._tools: dict[str, Tool[Any, Any]] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool[Any, Any]) -> None:
        spec = tool.spec
        if spec.name in self._tools:
            raise ToolRegistrationError(f"duplicate tool name {spec.name!r}")
        if not spec.capabilities:
            raise ToolRegistrationError(f"tool {spec.name!r} declares no capabilities")
        forbidden = spec.capabilities & FORBIDDEN_CAPABILITIES
        if forbidden:
            raise ToolRegistrationError(
                f"tool {spec.name!r} declares forbidden capabilities: {sorted(forbidden)}"
            )
        unknown = [c for c in spec.capabilities if c not in self._policy.rules]
        if unknown:
            raise ToolRegistrationError(
                f"tool {spec.name!r} declares capabilities unknown to policy: {sorted(unknown)}"
            )
        self._tools[spec.name] = tool

    def get(self, name: str) -> Tool[Any, Any] | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def capabilities(self) -> frozenset[str]:
        caps: set[str] = set()
        for tool in self._tools.values():
            caps |= tool.spec.capabilities
        return frozenset(caps)
