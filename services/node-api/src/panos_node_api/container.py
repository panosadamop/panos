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
from panos_core.checkpoints import CheckpointService
from panos_core.config import PanosSettings, ensure_directories
from panos_core.conversation import ConversationService
from panos_core.conversation.service import ContextFactory
from panos_core.domain.enums import PrivacyMode
from panos_core.domain.models import Node, Project
from panos_core.events import EventBus
from panos_core.jobs import JobService
from panos_core.jobs.context import JobContextFactory, JobServices
from panos_core.jobs.runner import JobRunner
from panos_core.jobs.workflows import CodingWorkflow, CommandWorkflow, ImpactAnalysisWorkflow
from panos_core.knowledge import KnowledgeIndex, KnowledgeService
from panos_core.knowledge.service import Embedder
from panos_core.nodes import ensure_local_node
from panos_core.notifications import EventChannel, LogChannel, NotificationService
from panos_core.observability import get_logger
from panos_core.persistence import UnitOfWorkFactory, create_engine, run_migrations
from panos_core.platform.desktop import DesktopAdapter, default_desktop
from panos_core.policy import PolicyEngine
from panos_core.projects import ProjectService
from panos_core.projects.context import project_tool_context
from panos_core.repository import AnalysisService
from panos_core.requirements import RequirementsService
from panos_core.requirements.pipeline import PipelineService
from panos_core.screen import ScreenService
from panos_core.secrets import SecretStore, create_secret_store, get_or_create_node_key
from panos_core.tools import ToolContext, ToolRegistry, ToolRuntime
from panos_core.tools.builtin import build_default_registry
from panos_core.tools.contract import CancelToken
from panos_core.voice.supervisor import VoiceChannel, VoiceSupervisor

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
    checkpoints: CheckpointService
    notifications: NotificationService
    runner: JobRunner
    requirements: RequirementsService
    pipeline: PipelineService
    voice: VoiceSupervisor
    screen: ScreenService
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
        await self.voice.stop()
        await self.runner.close()  # running jobs are recovered as interrupted on next start
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


def _job_context_factory(uow_factory: UnitOfWorkFactory, node_id: str) -> JobContextFactory:
    async def context_for(
        project: Project | None, *, actor: str, job_id: str, cancel: CancelToken
    ) -> ToolContext:
        if project is None:
            return ToolContext(actor=actor, node_id=node_id, job_id=job_id, cancel=cancel)
        return await project_tool_context(
            uow_factory, project, actor=actor, node_id=node_id, job_id=job_id, cancel=cancel
        )

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


async def _start_voice(voice: VoiceSupervisor) -> object:
    try:
        return await voice.start()
    except Exception:
        log.exception("voice_start_failed")
        return None


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
    checkpoints = CheckpointService(uow_factory, audit, settings)
    requirements = RequirementsService(uow_factory, audit)
    pipeline = PipelineService(
        uow_factory, audit, requirements, projects, gateway, settings.estimation
    )
    notifications = NotificationService([EventChannel(events), LogChannel()])
    runner = JobRunner(
        JobServices(
            jobs=jobs,
            tools=tools,
            gateway=gateway,
            checkpoints=checkpoints,
            analysis=analysis,
            uow_factory=uow_factory,
            settings=settings.jobs,
            context_for=_job_context_factory(uow_factory, node.id),
            requirements=requirements,
        ),
        [CodingWorkflow(), CommandWorkflow(), ImpactAnalysisWorkflow()],
        events,
        project_lookup=projects.get,
    )
    runner.add_listener(notifications.on_job_status)
    approvals.add_listener(runner.on_approval)
    screen = ScreenService(apps.desktop, gateway, audit)
    conversations = ConversationService(
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
    )

    async def voice_reply(text: str, conversation_id: str | None) -> tuple[str, str]:
        reply = await conversations.send(text, conversation_id=conversation_id, actor="user:voice")
        return reply.text, reply.conversation_id

    async def vocabulary() -> list[str]:
        # STT prompt: the names people say (projects, apps, tools) — ADR-016.
        names = [p.name for p in await projects.list()]
        names += [a.name for a in await apps.list()]
        return [*names, "Panos", "Docker", "Git", "commit", "branch", "pull request"]

    voice = VoiceSupervisor(
        settings.voice,
        settings.data_dir,
        settings.runtime_dir,
        events,
        voice_reply,
        jobs,
        runner,
        vocabulary,
    )
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
        conversations=conversations,
        checkpoints=checkpoints,
        notifications=notifications,
        runner=runner,
        requirements=requirements,
        pipeline=pipeline,
        voice=voice,
        screen=screen,
    )

    async def impact_task(project_id: str, changed_key: str, stale_keys: list[str]) -> None:
        # ADR-014: a changed approved item creates an impact-analysis task.
        project = await projects.get(project_id)
        await runner.submit(
            f"Impact analysis: {changed_key} changed",
            kind="impact_analysis",
            project=project,
            actor="system",
            input={"changed_key": changed_key, "stale_keys": stale_keys},
        )

    requirements.set_change_hook(impact_task)

    notifications.channels.append(VoiceChannel(voice))

    async def analyze_screen(question: str, project: Project | None, actor: str) -> str:
        try:
            result = await screen.analyze(question, project=project, actor=actor)
        except Exception as exc:  # explained to the user instead of failing the turn
            return f"I could not analyze the screen: {exc}"
        return result.text

    conversations.attach_screen(analyze_screen)
    if settings.voice.enabled:
        container.background.append(asyncio.create_task(_start_voice(voice)))
    await runner.recover()  # ADR-011: interrupted jobs become PAUSED; queued jobs start

    async def start_coding_job(objective: str, project: Project, actor: str) -> str:
        return (await runner.submit(objective, project=project, actor=actor)).id

    container.conversations.attach_job_starter(start_coding_job)
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
