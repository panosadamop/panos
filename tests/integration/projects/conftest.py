"""Real M3 service stack (projects, analysis, apps, knowledge) with a fake desktop."""

from dataclasses import dataclass

import pytest

from panos_core.approvals import ApprovalService
from panos_core.apps.service import AppService
from panos_core.audit import AuditService
from panos_core.config import PanosSettings
from panos_core.domain import Node, Project
from panos_core.jobs import JobService
from panos_core.knowledge import KnowledgeIndex, KnowledgeService
from panos_core.nodes import ensure_local_node
from panos_core.persistence import UnitOfWorkFactory
from panos_core.policy import PolicyEngine
from panos_core.projects import ProjectService
from panos_core.projects.context import project_tool_context
from panos_core.repository import AnalysisService
from panos_core.tools import ToolContext, ToolRuntime
from panos_core.tools.builtin import build_default_registry
from tests.fakes import FakeDesktop


@dataclass
class M3:
    uow: UnitOfWorkFactory
    node: Node
    audit: AuditService
    approvals: ApprovalService
    projects: ProjectService
    analysis: AnalysisService
    apps: AppService
    desktop: FakeDesktop
    knowledge: KnowledgeService
    runtime: ToolRuntime

    async def ctx(self, project: Project, actor: str = "agent:developer") -> ToolContext:
        return await project_tool_context(self.uow, project, actor=actor, node_id=self.node.id)


@pytest.fixture
async def m3(uow_factory: UnitOfWorkFactory, settings: PanosSettings) -> M3:
    node = await ensure_local_node(uow_factory)
    audit = AuditService(uow_factory, node.id)
    approvals = ApprovalService(uow_factory, audit, b"k" * 32)
    jobs = JobService(uow_factory, audit, node.id)
    desktop = FakeDesktop()
    apps = AppService(uow_factory, audit, desktop)
    await apps.refresh()
    analysis = AnalysisService(uow_factory, audit)
    knowledge = KnowledgeService(KnowledgeIndex(settings.data_dir / "knowledge.db"), audit)
    policy = PolicyEngine()
    runtime = ToolRuntime(
        registry=build_default_registry(
            policy.policy, apps=apps, snapshots=analysis, knowledge=knowledge
        ),
        policy=policy,
        approvals=approvals,
        audit=audit,
        jobs=jobs,
        uow_factory=uow_factory,
    )
    return M3(
        uow_factory,
        node,
        audit,
        approvals,
        ProjectService(uow_factory, audit, settings, node.id),
        analysis,
        apps,
        desktop,
        knowledge,
        runtime,
    )
