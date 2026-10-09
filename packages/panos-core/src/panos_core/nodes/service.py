"""Local node identity: created once, then reused across restarts."""

import platform
import socket
import sys

from panos_core.domain.clock import utc_now
from panos_core.domain.enums import NodeStatus
from panos_core.domain.models import Node
from panos_core.persistence.unit_of_work import UnitOfWorkFactory


def current_platform() -> str:
    """Normalized OS name: ``windows``, ``macos`` or ``linux`` (else the raw value)."""
    raw: str = sys.platform  # plain str: avoids mypy platform narrowing
    if raw == "win32":
        return "windows"
    if raw == "darwin":
        return "macos"
    if raw.startswith("linux"):
        return "linux"
    return raw


def _node_name() -> str:
    return socket.gethostname() or platform.node() or "panos-node"


async def ensure_local_node(uow_factory: UnitOfWorkFactory) -> Node:
    """Return the persisted local node, creating it on first start."""
    async with uow_factory() as uow:
        node = await uow.nodes.first()
        if node is None:
            node = Node(name=_node_name(), platform=current_platform())
            await uow.nodes.add(node)
        else:
            node.status = NodeStatus.ONLINE
            node.last_seen_at = utc_now()
            await uow.nodes.update(node)
        await uow.commit()
        return node
