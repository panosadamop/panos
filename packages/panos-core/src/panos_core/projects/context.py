"""Build a ToolContext for a project: persisted policy overrides + discovered commands."""

from panos_core.domain import Project
from panos_core.persistence.unit_of_work import UnitOfWorkFactory
from panos_core.tools.contract import CancelToken, ToolContext


async def project_tool_context(
    uow_factory: UnitOfWorkFactory,
    project: Project,
    *,
    actor: str,
    node_id: str,
    job_id: str | None = None,
    cancel: CancelToken | None = None,
) -> ToolContext:
    """The latest snapshot's format/lint/type-check/test/build commands become P1
    (PERMISSIONS.md "Run format/lint/unit tests"); everything else keeps its class."""
    async with uow_factory() as uow:
        snapshot = await uow.snapshots.latest(project.id)
    return ToolContext(
        actor=actor,
        node_id=node_id,
        project=project,
        job_id=job_id,
        cancel=cancel or CancelToken(),
        policy_overrides=dict(project.policy_overrides),
        allowed_commands=snapshot.allowlisted_commands() if snapshot else (),
    )
