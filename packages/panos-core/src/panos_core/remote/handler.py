"""The node's answers to hub requests (ADR-028 §4–7, AC-US061-03, AC-US062-02).

Everything goes through this node's own services:
* a dispatched job is submitted to the local runner, so the local policy, project
  overrides and approvals apply;
* a remote approval decision goes through the local approval service, which checks it and
  issues the single-use token here; the hub never sees tokens;
* queries are read-only and limited to the allowlisted views.

LOCAL_ONLY projects: step summaries and approval summaries are left out of what is
*published* to the hub. A live ``query`` may return them for display (they are not stored
by the hub).
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from panos_core import API_VERSION, __version__
from panos_core.approvals import ApprovalService
from panos_core.approvals.service import ApprovalError
from panos_core.audit import AuditService
from panos_core.config import PanosSettings
from panos_core.domain import Node, Project
from panos_core.domain.enums import JobStatus, PrivacyMode
from panos_core.domain.errors import EntityNotFoundError
from panos_core.domain.models import ActionApproval, Job, JobStep
from panos_core.jobs import JobService
from panos_core.jobs.runner import JobError, JobRunner
from panos_core.persistence import UnitOfWorkFactory
from panos_core.projects import ProjectService
from panos_core.projects.service import ProjectError
from panos_core.remote.link import RemoteRefusalError
from panos_core.remote.protocol import (
    ApprovalUpdate,
    Control,
    Decision,
    Dispatch,
    Hello,
    Inventory,
    JobUpdate,
    ProjectInfo,
    Query,
    StepInfo,
)
from panos_core.requirements import RequirementsService

ACTIVE: Final = (
    JobStatus.QUEUED,
    JobStatus.RUNNING,
    JobStatus.WAITING_FOR_APPROVAL,
    JobStatus.WAITING_FOR_USER,
    JobStatus.PAUSED,
)
SNAPSHOT_RECENT: Final = 50


def _local(privacy: PrivacyMode | str | None) -> bool:
    return str(privacy or PrivacyMode.LOCAL_ONLY) == PrivacyMode.LOCAL_ONLY.value


def job_update(job: Job, steps: Sequence[JobStep], project: Project | None) -> JobUpdate:
    privacy = project.privacy_mode if project else PrivacyMode.LOCAL_ONLY
    hide = _local(privacy)
    return JobUpdate(
        job_id=job.id,
        kind=job.kind,
        objective=job.objective[:10_000],
        status=job.status.value,
        status_reason=None if hide else (job.status_reason or None),
        phase=job.state.get("phase"),
        project_id=job.project_id,
        project_name=project.name if project else None,
        privacy_mode=privacy.value,
        dispatch_id=job.input.get("dispatch_id"),
        created_at=job.created_at,
        completed_at=job.completed_at,
        steps=[
            StepInfo(
                sequence=s.sequence,
                agent=s.agent_type,
                tool=s.tool_name,
                status=s.status.value,
                summary=None
                if hide
                else (s.output_summary or s.input_summary or "")[:2000] or None,
            )
            for s in steps[-500:]
        ],
    )


def approval_update(approval: ActionApproval, project: Project | None) -> ApprovalUpdate:
    privacy = project.privacy_mode if project else PrivacyMode.LOCAL_ONLY
    return ApprovalUpdate(
        approval_id=approval.id,
        job_id=approval.job_id,
        project_id=approval.project_id,
        action_type=approval.action_type,
        target=approval.target[:2000],
        payload_hash=approval.payload_hash,
        summary=None if _local(privacy) else approval.summary[:10_000],
        status=approval.status.value,
        privacy_mode=privacy.value,
        requested_at=approval.requested_at,
        expires_at=approval.expires_at,
    )


class NodeRemoteHandler:
    def __init__(
        self,
        *,
        settings: PanosSettings,
        node: Node,
        projects: ProjectService,
        runner: JobRunner,
        jobs: JobService,
        approvals: ApprovalService,
        audit: AuditService,
        requirements: RequirementsService,
        uow_factory: UnitOfWorkFactory,
        tools: Callable[[], list[str]],
        features: Sequence[str] = (),
        name: str | None = None,
    ) -> None:
        self._settings = settings
        self._node = node
        self._projects = projects
        self._runner = runner
        self._jobs = jobs
        self._approvals = approvals
        self._audit = audit
        self._requirements = requirements
        self._uow_factory = uow_factory
        self._tools = tools
        self._features = list(features)
        self._name = name or node.name

    # ------------------------------------------------------------------ announcements
    async def hello(self) -> Hello:
        return Hello(
            node_id=self._node.id,
            name=self._name[:128],
            platform=self._node.platform,
            version=__version__,
            api_version=API_VERSION,
            tools=self._tools(),
            features=self._features,
            accepts_remote_approvals=self._settings.remote.accept_remote_approvals,
        )

    async def inventory(self) -> Inventory:
        return Inventory(
            projects=[
                ProjectInfo(id=p.id, name=p.name, privacy_mode=p.privacy_mode.value)
                for p in await self._projects.list()
            ]
        )

    async def _project(self, project_id: str | None) -> Project | None:
        if project_id is None:
            return None
        try:
            return await self._projects.get(project_id)
        except ProjectError:
            return None

    async def job_message(self, job: Job) -> JobUpdate:
        return job_update(job, await self._jobs.steps(job.id), await self._project(job.project_id))

    async def approval_message(self, approval: ActionApproval) -> ApprovalUpdate:
        return approval_update(approval, await self._project(approval.project_id))

    async def snapshot(self) -> list[JobUpdate | ApprovalUpdate]:
        jobs = list(await self._jobs.list(limit=SNAPSHOT_RECENT))
        items: list[JobUpdate | ApprovalUpdate] = [await self.job_message(j) for j in jobs]
        items += [await self.approval_message(a) for a in await self._approvals.list_pending()]
        return items

    async def running_jobs(self) -> int:
        return sum(1 for j in await self._jobs.list(statuses=[JobStatus.RUNNING], limit=500))

    # ------------------------------------------------------------------ requests
    async def dispatch(self, msg: Dispatch) -> dict[str, Any]:
        project = None
        if msg.project:
            try:
                project = await self._projects.get(msg.project)
            except ProjectError as exc:
                raise RemoteRefusalError(f"this PC has no project {msg.project!r}") from exc
        if msg.kind == "coding" and project is None:
            raise RemoteRefusalError("a coding job needs a project")
        if msg.kind == "command" and not msg.argv:
            raise RemoteRefusalError("a command job needs argv")
        job_input: dict[str, Any] = {
            "dispatch_id": msg.dispatch_id,
            "requested_by": msg.requested_by,
        }
        if msg.kind == "coding":
            job_input["commit"] = True
        elif msg.kind == "command":
            job_input["argv"] = msg.argv
        else:
            job_input |= {"question": msg.objective, "urls": msg.urls or []}
        actor = f"remote:{msg.requested_by}"
        try:
            job = await self._runner.submit(
                msg.objective, kind=msg.kind, project=project, input=job_input, actor=actor
            )
        except JobError as exc:
            raise RemoteRefusalError(str(exc)) from exc
        await self._audit.record(
            actor=actor,
            event_type="remote.dispatch_accepted",
            action="job.create",
            project_id=project.id if project else None,
            job_id=job.id,
            metadata={"dispatch_id": msg.dispatch_id, "kind": msg.kind},
        )
        return {"job_id": job.id, "project_id": project.id if project else None}

    async def control(self, msg: Control) -> dict[str, Any]:
        actor = f"remote:{msg.requested_by}"
        action = {
            "pause": self._runner.pause,
            "resume": self._runner.resume,
            "cancel": self._runner.cancel,
        }
        try:
            job = await action[msg.action](msg.job_id, actor=actor)
        except (JobError, EntityNotFoundError) as exc:
            raise RemoteRefusalError(str(exc)) from exc
        return {"job_id": job.id, "status": job.status.value}

    async def decision(self, msg: Decision) -> dict[str, Any]:
        if not self._settings.remote.accept_remote_approvals:
            raise RemoteRefusalError("this PC does not accept approvals from the hub")
        approval = await self._approvals.get(msg.approval_id)
        if approval is None:
            raise RemoteRefusalError(f"no approval {msg.approval_id}")
        if approval.job_id is None:
            # The token of an interactive approval goes back to whoever asked on this PC.
            raise RemoteRefusalError("decide interactive approvals on this PC")
        try:
            done = await self._approvals.decide(
                msg.approval_id,
                approve=msg.approve,
                decided_by=f"{msg.decided_by}@hub",
                comment=msg.comment,
            )
        except ApprovalError as exc:
            raise RemoteRefusalError(str(exc)) from exc
        return {"approval_id": done.approval.id, "status": done.approval.status.value}

    async def query(self, msg: Query) -> Any:
        p = msg.params
        if msg.name == "projects":
            return (await self.inventory()).model_dump()["projects"]
        if msg.name == "job":
            try:
                job = await self._jobs.get(str(p.get("job_id")))
            except EntityNotFoundError as exc:
                raise RemoteRefusalError(str(exc)) from exc
            steps = await self._jobs.steps(job.id)
            project = await self._project(job.project_id)
            # Live view: full summaries for display, never stored by the hub.
            return {
                **job_update(job, steps, None).model_dump(mode="json"),
                "privacy_mode": (project.privacy_mode.value if project else "LOCAL_ONLY"),
                "project_name": project.name if project else None,
                "status_reason": job.status_reason,
                "result": _result_view(job.result),
            }
        if msg.name == "approval":
            approval = await self._approvals.get(str(p.get("approval_id")))
            if approval is None:
                raise RemoteRefusalError("no such approval")
            return {
                **approval_update(approval, None).model_dump(mode="json"),
                "summary": approval.summary,
            }
        if msg.name == "audit":
            limit = max(1, min(int(p.get("limit") or 100), 500))
            events = await self._audit.list(limit=limit, job_id=_str(p.get("job_id")))
            return [
                {
                    "created_at": e.created_at.isoformat(),
                    "actor": e.actor,
                    "event_type": e.event_type,
                    "action": e.action,
                    "project_id": e.project_id,
                    "job_id": e.job_id,
                    "policy_decision": e.policy_decision,
                }
                for e in events
            ]
        if msg.name == "usage":
            return await self._usage(int(p.get("days") or 30))
        if msg.name == "traceability":
            ref = _str(p.get("project"))
            if not ref:
                raise RemoteRefusalError("traceability needs a project")
            try:
                project = await self._projects.get(ref)
            except ProjectError as exc:
                raise RemoteRefusalError(str(exc)) from exc
            return await self._requirements.traceability(project.id)
        raise RemoteRefusalError(f"unknown query {msg.name}")  # pragma: no cover

    async def _usage(self, days: int) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(days=max(1, min(days, 366)))
        async with self._uow_factory() as uow:
            records = await uow.ai_usage.since(since)
        rows: dict[tuple[str, str, str], dict[str, Any]] = {}
        for r in records:
            row = rows.setdefault(
                (r.provider, r.model, r.locality),
                {
                    "provider": r.provider,
                    "model": r.model,
                    "locality": r.locality,
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            row["calls"] += 1
            row["input_tokens"] += r.input_tokens
            row["output_tokens"] += r.output_tokens
            row["cost_usd"] = round(row["cost_usd"] + r.cost_usd, 6)
        return {
            "since": since.isoformat(),
            "total_cost_usd": round(sum(r.cost_usd for r in records), 6),
            "rows": list(rows.values()),
        }


def _str(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def _result_view(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """The report without bulky or tool-output fields (diffs stay on the node)."""
    if not result:
        return None
    keep = (
        "success",
        "summary",
        "reason",
        "answer",
        "sources",
        "cited",
        "changed_files",
        "unresolved",
    )
    return {k: result[k] for k in keep if k in result}


__all__ = ["NodeRemoteHandler", "approval_update", "job_update"]
