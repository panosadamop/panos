"""Built-in tools and the default registry."""

from typing import Any

from panos_core.apps.service import AppService
from panos_core.browser import BrowserEngine
from panos_core.connectors.service import ConnectorService
from panos_core.knowledge.collections import CollectionService
from panos_core.knowledge.service import KnowledgeService
from panos_core.policy import Policy
from panos_core.requirements.service import RequirementsService
from panos_core.tools.builtin.browser import BrowserReadTool, BrowserSearchTool
from panos_core.tools.builtin.calendar import CALENDAR_TOOLS
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
from panos_core.tools.builtin.docker import DockerInspectTool
from panos_core.tools.builtin.fs import FS_TOOLS
from panos_core.tools.builtin.git import GIT_TOOLS
from panos_core.tools.builtin.mail import MAIL_TOOLS
from panos_core.tools.builtin.shell import ShellTool
from panos_core.tools.builtin.workitems import WORKITEM_TOOLS, WorkItemChangeTool
from panos_core.tools.contract import Tool
from panos_core.tools.registry import ToolRegistry


def default_tools(
    *,
    apps: AppService | None = None,
    snapshots: SnapshotSource | None = None,
    knowledge: KnowledgeService | None = None,
    connectors: ConnectorService | None = None,
    requirements: RequirementsService | None = None,
    collections: CollectionService | None = None,
    browser: BrowserEngine | None = None,
    allow_private_web: bool = False,
) -> list[Tool[Any, Any]]:
    tools: list[Tool[Any, Any]] = [
        *(cls() for cls in FS_TOOLS),
        ShellTool(),
        DockerInspectTool(),
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
        tools.append(KnowledgeSearchTool(knowledge, collections))
    if connectors is not None:
        tools += [cls(connectors) for cls in (*MAIL_TOOLS, *CALENDAR_TOOLS, *WORKITEM_TOOLS)]
        tools.append(WorkItemChangeTool(connectors, requirements))
    if browser is not None:
        tools += [
            BrowserReadTool(browser, allow_private=allow_private_web),
            BrowserSearchTool(browser),
        ]
    return tools


def build_default_registry(
    policy: Policy,
    *,
    apps: AppService | None = None,
    snapshots: SnapshotSource | None = None,
    knowledge: KnowledgeService | None = None,
    connectors: ConnectorService | None = None,
    requirements: RequirementsService | None = None,
    collections: CollectionService | None = None,
    browser: BrowserEngine | None = None,
    allow_private_web: bool = False,
) -> ToolRegistry:
    """The default runtime profile: no email-send (AC-US043-02) and no browser submit or
    upload capability (AC-US045-02)."""
    return ToolRegistry(
        policy,
        default_tools(
            apps=apps,
            snapshots=snapshots,
            knowledge=knowledge,
            connectors=connectors,
            requirements=requirements,
            collections=collections,
            browser=browser,
            allow_private_web=allow_private_web,
        ),
    )


__all__ = ["build_default_registry", "default_tools"]
