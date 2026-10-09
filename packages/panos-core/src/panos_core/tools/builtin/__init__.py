"""Built-in tools and the default registry."""

from typing import Any

from panos_core.policy import Policy
from panos_core.tools.builtin.fs import FS_TOOLS
from panos_core.tools.builtin.shell import ShellTool
from panos_core.tools.contract import Tool
from panos_core.tools.registry import ToolRegistry


def default_tools() -> list[Tool[Any, Any]]:
    return [*(cls() for cls in FS_TOOLS), ShellTool()]


def build_default_registry(policy: Policy) -> ToolRegistry:
    """The default runtime profile. It contains no email-send capability (AC-US043-02)."""
    return ToolRegistry(policy, default_tools())


__all__ = ["build_default_registry", "default_tools"]
