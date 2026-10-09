"""Built-in tools and the default registry."""

from typing import Any

from panos_core.apps.service import AppService
from panos_core.knowledge.service import KnowledgeService
from panos_core.policy import Policy
from panos_core.tools.builtin.desktop import (
    AppFocusTool,
    AppLaunchTool,
    AppListTool,
    KnowledgeSearchTool,
    OpenPathTool,
    ProjectOpenTool,
    SnapshotSource,
    WindowActiveTool,
)
from panos_core.tools.builtin.fs import FS_TOOLS
from panos_core.tools.builtin.git import GIT_TOOLS
from panos_core.tools.builtin.shell import ShellTool
from panos_core.tools.contract import Tool
from panos_core.tools.registry import ToolRegistry


def default_tools(
    *,
    apps: AppService | None = None,
    snapshots: SnapshotSource | None = None,
    knowledge: KnowledgeService | None = None,
) -> list[Tool[Any, Any]]:
    tools: list[Tool[Any, Any]] = [
        *(cls() for cls in FS_TOOLS),
        ShellTool(),
        *(cls() for cls in GIT_TOOLS),
    ]
    if apps is not None:
        tools += [
            AppListTool(apps),
            AppLaunchTool(apps),
            OpenPathTool(apps),
            WindowActiveTool(apps),
            AppFocusTool(apps),
        ]
        if snapshots is not None:
            tools.append(ProjectOpenTool(apps, snapshots))
    if knowledge is not None:
        tools.append(KnowledgeSearchTool(knowledge))
    return tools


def build_default_registry(
    policy: Policy,
    *,
    apps: AppService | None = None,
    snapshots: SnapshotSource | None = None,
    knowledge: KnowledgeService | None = None,
) -> ToolRegistry:
    """The default runtime profile. It contains no email-send capability (AC-US043-02)."""
    return ToolRegistry(policy, default_tools(apps=apps, snapshots=snapshots, knowledge=knowledge))


__all__ = ["build_default_registry", "default_tools"]
