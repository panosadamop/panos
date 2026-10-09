"""Notifications (FR-025, US-051, AC-US050-03, AC-US051-01/02).

Only events that need the user produce a notification: approvals, blockers, questions,
completion and failure. Routine progress is never notified (AC-US051-02).

Channels are pluggable: the event bus (``notification`` events for the desktop app and
``/ws/events``) and the log are built in; desktop toasts and voice arrive in M7.
"""

from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from panos_core.domain.enums import JobStatus
from panos_core.domain.models import Job
from panos_core.events import EventBus
from panos_core.observability import get_logger

log = get_logger(__name__)

Kind = Literal["approval", "blocked", "question", "completed", "failed", "cancelled"]

_FOR_STATUS: Final[dict[JobStatus, tuple[Kind, str]]] = {
    JobStatus.WAITING_FOR_APPROVAL: ("approval", "needs your approval"),
    JobStatus.WAITING_FOR_USER: ("question", "needs your input"),
    JobStatus.BLOCKED: ("blocked", "is blocked"),
    JobStatus.COMPLETED: ("completed", "completed"),
    JobStatus.FAILED: ("failed", "failed"),
}


@dataclass(frozen=True)
class Notification:
    kind: Kind
    title: str
    body: str
    job_id: str | None = None
    project_id: str | None = None
    priority: Literal["normal", "high"] = "normal"


class Channel(Protocol):
    name: str

    async def send(self, notification: Notification) -> None: ...


@dataclass
class EventChannel:
    events: EventBus
    name: str = "events"

    async def send(self, notification: Notification) -> None:
        self.events.publish(
            "notification",
            {
                "kind": notification.kind,
                "title": notification.title,
                "body": notification.body,
                "project_id": notification.project_id,
                "priority": notification.priority,
            },
            job_id=notification.job_id,
        )


@dataclass
class LogChannel:
    name: str = "log"

    async def send(self, notification: Notification) -> None:
        log.info(
            "notification",
            kind=notification.kind,
            title=notification.title,
            job_id=notification.job_id,
        )


@dataclass
class NotificationService:
    channels: list[Channel] = field(default_factory=list)
    sent: list[Notification] = field(default_factory=list)  # recent history (bounded)

    async def notify(self, notification: Notification) -> None:
        self.sent = [*self.sent[-199:], notification]
        for channel in self.channels:
            try:
                await channel.send(notification)
            except Exception:
                log.exception("notification_channel_failed", channel=channel.name)

    async def on_job_status(self, job: Job) -> None:
        """Job status listener: maps user-relevant transitions to notifications."""
        mapping = _FOR_STATUS.get(job.status)
        if mapping is None:
            return  # QUEUED/RUNNING/PAUSED/...: routine, not notified
        if job.status == JobStatus.BLOCKED and job.status_reason == "":
            return
        kind, verb = mapping
        await self.notify(
            Notification(
                kind=kind,
                title=f"Job {verb}: {job.objective[:80]}",
                body=job.status_reason or verb,
                job_id=job.id,
                project_id=job.project_id,
                priority="high"
                if kind in ("approval", "blocked", "question", "failed")
                else "normal",
            )
        )
