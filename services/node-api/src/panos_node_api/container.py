"""Composition root for the node: wires settings, persistence, identity, policy and tools."""

import asyncio
import contextlib
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine

from panos_core.ai.adapters import build_providers
from panos_core.ai.adapters.ollama import SpeedCallback
from panos_core.ai.gateway import AIGateway, CallContext
from panos_core.ai.ports import ChatProvider
from panos_core.ai.registry import ModelRegistry
from panos_core.approvals import ApprovalService
from panos_core.apps.service import AppService
from panos_core.audit import AuditService
from panos_core.config import PanosSettings, ensure_directories
from panos_core.conversation import ConversationService
from panos_core.conversation.service import ContextFactory
from panos_core.domain.enums import PrivacyMode
from panos_core.domain.models import Node, Project
from panos_core.events import EventBus
from panos_core.jobs import JobService
from panos_core.knowledge import KnowledgeIndex, KnowledgeService
from panos_core.knowledge.service import Embedder
from panos_core.nodes import ensure_local_node
from panos_core.observability import get_logger
from panos_core.persistence import UnitOfWorkFactory, create_engine, run_migrations
from panos_core.platform.desktop import DesktopAdapter, default_desktop
from panos_core.policy import PolicyEngine
from panos_core.projects import ProjectService
from panos_core.projects.context import project_tool_context
from panos_core.repository import AnalysisService
from panos_core.secrets import SecretStore, create_secret_store, get_or_create_node_key
from panos_core.tools import ToolContext, ToolRegistry, ToolRuntime
from panos_core.tools.builtin import build_default_registry

log = get_logger(__name__)

KNOWLEDGE_DB = "knowledge.db"


@dataclass
class NodeContainer:
    settings: PanosSettings
    engine: AsyncEngine
    uow_factory: UnitOfWorkFactory
    node: Node
    secrets: SecretStore
    policy: PolicyEngine
    audit: AuditService
    approvals: ApprovalService
    jobs: JobService
    projects: ProjectService
    analysis: AnalysisService
    apps: AppService
    knowledge: KnowledgeService
    registry: ToolRegistry
    tools: ToolRuntime
    models: ModelRegistry
    gateway: AIGateway
    events: EventBus
    conversations: ConversationService
    started_monotonic: float = field(default_factory=time.monotonic)
    background: list[asyncio.Task[object]] = field(default_factory=list)

    @property
    def uptime_s(self) -> float:
        return time.monotonic() - self.started_monotonic

    async def context_for(self, project: Project | None, *, actor: str = "user") -> ToolContext:
        return await _context_factory(self.uow_factory, self.node.id)(project, actor=actor)

    async def reload_providers(self) -> None:
        """Rebuild AI providers, e.g. after an API key was set or cleared."""
        old = self.gateway.providers
        self.gateway.replace_providers(_providers(self.settings, self.models, self.secrets))
        for provider in old.values():
            with contextlib.suppress(Exception):
                await provider.close()
        await self.gateway.refresh_health(force=True)

    async def close(self) -> None:
        for task in self.background:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.gateway.close()
        await self.engine.dispose()


def _context_factory(uow_factory: UnitOfWorkFactory, node_id: str) -> ContextFactory:
    async def context_for(project: Project | None, *, actor: str = "user") -> ToolContext:
        if project is None:
            return ToolContext(actor=actor, node_id=node_id)
        return await project_tool_context(uow_factory, project, actor=actor, node_id=node_id)

    return context_for


def _providers(
    settings: PanosSettings, models: ModelRegistry, secrets: SecretStore
) -> dict[str, ChatProvider]:
    kinds = {p.id: p.kind for p in settings.ai.providers}

    def speed(provider_id: str) -> SpeedCallback:
        return lambda model, ptps, gtps: models.update_speed(kinds[provider_id], model, ptps, gtps)

    return build_providers(
        settings.ai, models, secrets, on_speed={pid: speed(pid) for pid in kinds}
    )


def _embedder(gateway: AIGateway) -> Embedder:
    async def embed(
        texts: Sequence[str], labels: Sequence[PrivacyMode], *, query: bool
    ) -> tuple[list[list[float]], str]:
        vectors, model = await gateway.embed(
            texts, labels, CallContext(actor="system"), query=query
        )
        return vectors, model.name

    return embed


async def _refresh_apps(apps: AppService) -> object:
    try:
        return await apps.refresh(actor="system")
    except Exception:
        log.exception("app_discovery_failed")
        return None


@dataclass(frozen=True)
class AIOverride:
    """Test/dev seam: use these providers and models instead of the configured ones."""

    providers: dict[str, ChatProvider]
    models: ModelRegistry


async def build_container(
    settings: PanosSettings,
    *,
    desktop: DesktopAdapter | None = None,
    ai: AIOverride | None = None,
) -> NodeContainer:
    ensure_directories(settings)
    await asyncio.to_thread(run_migrations, settings.database_path)
    engine = create_engine(settings.database_path)
    uow_factory = UnitOfWorkFactory(engine)
    node = await ensure_local_node(uow_factory)

    if settings.secret_backend == "memory":  # noqa: S105 - backend name, not a secret
        log.warning("secret_backend_memory", detail="test-only backend; secrets are not kept")
    secrets = create_secret_store(settings.secret_backend)
    policy = PolicyEngine()
    audit = AuditService(uow_factory, node.id)
    approvals = ApprovalService(uow_factory, audit, get_or_create_node_key(secrets))
    jobs = JobService(uow_factory, audit, node.id)
    projects = ProjectService(uow_factory, audit, settings, node.id)
    analysis = AnalysisService(uow_factory, audit)
    apps = AppService(uow_factory, audit, desktop or default_desktop())
    knowledge = KnowledgeService(KnowledgeIndex(settings.data_dir / KNOWLEDGE_DB), audit)
    registry = build_default_registry(
        policy.policy, apps=apps, snapshots=analysis, knowledge=knowledge
    )
    tools = ToolRuntime(
        registry=registry,
        policy=policy,
        approvals=approvals,
        audit=audit,
        jobs=jobs,
        uow_factory=uow_factory,
    )
    models = ai.models if ai else ModelRegistry()
    gateway = AIGateway(
        ai.providers if ai else _providers(settings, models, secrets),
        models,
        uow_factory,
        audit,
        settings.ai,
    )
    knowledge.attach_embedder(_embedder(gateway))
    events = EventBus()
    container = NodeContainer(
        settings=settings,
        engine=engine,
        uow_factory=uow_factory,
        node=node,
        secrets=secrets,
        policy=policy,
        audit=audit,
        approvals=approvals,
        jobs=jobs,
        projects=projects,
        analysis=analysis,
        apps=apps,
        knowledge=knowledge,
        registry=registry,
        tools=tools,
        models=models,
        gateway=gateway,
        events=events,
        conversations=ConversationService(
            uow_factory,
            gateway,
            knowledge,
            projects,
            apps,
            tools,
            audit,
            events,
            settings.ai,
            node.id,
            _context_factory(uow_factory, node.id),
        ),
    )
    # Discovery reads the registry/filesystem; run it after startup so /health is not delayed.
    container.background.append(asyncio.create_task(_refresh_apps(apps)))
    log.info(
        "node_ready",
        node_id=node.id,
        platform=node.platform,
        database=str(settings.database_path),
        tools=registry.names(),
    )
    return container
