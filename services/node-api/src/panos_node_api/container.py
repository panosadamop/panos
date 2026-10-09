"""Composition root for the node: wires settings, persistence, identity, policy and tools."""

import asyncio
import time
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine

from panos_core.approvals import ApprovalService
from panos_core.audit import AuditService
from panos_core.config import PanosSettings, ensure_directories
from panos_core.domain.models import Node
from panos_core.jobs import JobService
from panos_core.nodes import ensure_local_node
from panos_core.observability import get_logger
from panos_core.persistence import UnitOfWorkFactory, create_engine, run_migrations
from panos_core.policy import PolicyEngine
from panos_core.secrets import SecretStore, create_secret_store, get_or_create_node_key
from panos_core.tools import ToolRegistry, ToolRuntime
from panos_core.tools.builtin import build_default_registry

log = get_logger(__name__)


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
    registry: ToolRegistry
    tools: ToolRuntime
    started_monotonic: float = field(default_factory=time.monotonic)

    @property
    def uptime_s(self) -> float:
        return time.monotonic() - self.started_monotonic

    async def close(self) -> None:
        await self.engine.dispose()


async def build_container(settings: PanosSettings) -> NodeContainer:
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
    registry = build_default_registry(policy.policy)
    tools = ToolRuntime(
        registry=registry,
        policy=policy,
        approvals=approvals,
        audit=audit,
        jobs=jobs,
        uow_factory=uow_factory,
    )
    log.info(
        "node_ready",
        node_id=node.id,
        platform=node.platform,
        database=str(settings.database_path),
        tools=registry.names(),
    )
    return NodeContainer(
        settings=settings,
        engine=engine,
        uow_factory=uow_factory,
        node=node,
        secrets=secrets,
        policy=policy,
        audit=audit,
        approvals=approvals,
        jobs=jobs,
        registry=registry,
        tools=tools,
    )
