"""Connector service and tool runtime on a real database, in-memory secrets and a fake cloud."""

from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from panos_core.approvals import ApprovalService
from panos_core.audit import AuditService
from panos_core.connectors.service import ConnectorService
from panos_core.domain import Node
from panos_core.domain.connectors import ConnectorAccount, ConnectorKind
from panos_core.jobs import JobService
from panos_core.nodes import ensure_local_node
from panos_core.persistence import UnitOfWorkFactory
from panos_core.policy import PolicyEngine
from panos_core.requirements import RequirementsService
from panos_core.secrets import InMemorySecretStore
from panos_core.tools import ToolContext, ToolRuntime
from panos_core.tools.builtin import build_default_registry
from tests.connector_fakes import FakeCloud

NODE_KEY = b"k" * 32


@dataclass
class Env:
    service: ConnectorService
    cloud: FakeCloud
    secrets: InMemorySecretStore
    audit: AuditService
    uow_factory: UnitOfWorkFactory
    approvals: ApprovalService
    runtime: ToolRuntime
    node: Node
    requirements: RequirementsService
    opened: list[str] = field(default_factory=list)

    def ctx(self, actor: str = "user") -> ToolContext:
        return ToolContext(actor=actor, node_id=self.node.id)


@pytest.fixture
async def env(uow_factory: UnitOfWorkFactory) -> Env:
    node = await ensure_local_node(uow_factory)
    audit = AuditService(uow_factory, node.id)
    cloud = FakeCloud()
    secrets = InMemorySecretStore()
    opened: list[str] = []
    service = ConnectorService(
        uow_factory, secrets, audit, transport=cloud.transport(), open_url=opened.append
    )
    policy = PolicyEngine()
    requirements = RequirementsService(uow_factory, audit)
    approvals = ApprovalService(uow_factory, audit, NODE_KEY)
    runtime = ToolRuntime(
        registry=build_default_registry(
            policy.policy, connectors=service, requirements=requirements
        ),
        policy=policy,
        approvals=approvals,
        audit=audit,
        jobs=JobService(uow_factory, audit, node.id),
        uow_factory=uow_factory,
    )
    return Env(
        service, cloud, secrets, audit, uow_factory, approvals, runtime, node, requirements, opened
    )


async def connect_google(env: Env, features: list[str]) -> ConnectorAccount:
    """Run the real loopback OAuth flow against the fake Google."""
    if env.service.client_registration(ConnectorKind.GOOGLE) is None:
        env.service.set_client(ConnectorKind.GOOGLE, "cid", "csecret")
    flow = await env.service.start_google(features, open_browser=False)
    params = {k: v[0] for k, v in parse_qs(urlsplit(flow.auth_url or "").query).items()}
    async with httpx.AsyncClient() as browser:
        await browser.get(params["redirect_uri"], params={"code": "c", "state": params["state"]})
    assert flow.task is not None
    await flow.task
    assert flow.status == "connected", flow.error
    return await env.service.get(flow.account_id or "")
