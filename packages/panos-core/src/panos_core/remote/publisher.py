"""Keep the hub's mirror of this node current (ADR-028 §4, US-060/062).

Job status changes are pushed at once (runner listener). A short sweep also pushes step
progress of active jobs and approvals as they appear or change; unchanged messages are not
resent. While the link is down nothing is queued: reconnecting sends a full snapshot.
"""

import asyncio
import contextlib
import hashlib
from typing import Final

from panos_core.approvals import ApprovalService
from panos_core.domain.models import ActionApproval, Job
from panos_core.jobs import JobService
from panos_core.remote.handler import ACTIVE, NodeRemoteHandler
from panos_core.remote.link import HubLink
from panos_core.remote.protocol import ApprovalUpdate, Inventory, JobUpdate

SWEEP_S: Final = 2.0


class RemotePublisher:
    def __init__(
        self,
        link: HubLink,
        handler: NodeRemoteHandler,
        jobs: JobService,
        approvals: ApprovalService,
        *,
        sweep_s: float = SWEEP_S,
    ) -> None:
        self._link = link
        self._handler = handler
        self._jobs = jobs
        self._approvals = approvals
        self._sweep_s = sweep_s
        self._sent: dict[str, str] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="hub-publisher")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def on_job_status(self, job: Job) -> None:
        self._push(await self._handler.job_message(job))

    async def on_approval_decided(self, approval: ActionApproval, token: str | None) -> None:
        self._push(await self._handler.approval_message(approval))

    def _push(self, message: JobUpdate | ApprovalUpdate | Inventory) -> None:
        if self._link.state != "connected":
            self._sent.clear()  # a reconnect sends a snapshot; start fresh afterwards
            return
        if isinstance(message, Inventory):
            key = "inventory"
        else:
            key = message.job_id if isinstance(message, JobUpdate) else message.approval_id
        digest = hashlib.sha256(message.model_dump_json().encode()).hexdigest()
        if self._sent.get(key) == digest:
            return
        self._sent[key] = digest
        self._link.publish(message)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._sweep_s)
            if self._link.state != "connected":
                self._sent.clear()
                continue
            with contextlib.suppress(Exception):
                self._push(await self._handler.inventory())  # e.g. a project was registered
                for job in await self._jobs.list(statuses=list(ACTIVE), limit=50):
                    self._push(await self._handler.job_message(job))
                for approval in await self._approvals.list_pending():
                    self._push(await self._handler.approval_message(approval))
