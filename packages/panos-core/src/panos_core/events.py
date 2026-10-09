"""In-process event bus feeding ``/ws/events`` (API.md, ADR-018).

Envelope: ``{v, type, id, ts, correlation_id, job_id?, payload}``. Subscribers get a
bounded queue; a slow subscriber loses its oldest events rather than blocking Panos.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from panos_core.domain.clock import utc_now
from panos_core.observability import get_correlation_id

EVENT_SCHEMA_VERSION = 1
QUEUE_SIZE = 1000


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    v: int = EVENT_SCHEMA_VERSION
    type: str
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: str = Field(default_factory=lambda: utc_now().isoformat())
    correlation_id: str | None = None
    job_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()

    def publish(self, type_: str, payload: dict[str, Any], *, job_id: str | None = None) -> Event:
        event = Event(
            type=type_, payload=payload, job_id=job_id, correlation_id=get_correlation_id()
        )
        for queue in list(self._subscribers):
            if queue.full():
                queue.get_nowait()  # drop the oldest event for this slow subscriber
            queue.put_nowait(event)
        return event

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
