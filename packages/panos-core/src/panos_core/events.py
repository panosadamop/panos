"""In-process event bus feeding ``/ws/events`` (API.md, ADR-018).

Envelope: ``{v, seq, type, id, ts, correlation_id, job_id?, payload}``. ``seq`` increases by
one per event for the lifetime of the node. Subscribers get a bounded queue; a slow
subscriber loses its oldest events rather than blocking Panos.

Reconnect and resync (M7-04): the last ``REPLAY_SIZE`` events are kept in memory. A client
that reconnects with the last ``seq`` it saw gets the missed events replayed; if they are no
longer available (or the node restarted) it is told to resync, i.e. reload its state.
"""

import asyncio
import uuid
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from panos_core.domain.clock import utc_now
from panos_core.observability import get_correlation_id

EVENT_SCHEMA_VERSION = 1
QUEUE_SIZE = 1000
REPLAY_SIZE = 2000


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    v: int = EVENT_SCHEMA_VERSION
    seq: int = 0
    type: str
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: str = Field(default_factory=lambda: utc_now().isoformat())
    correlation_id: str | None = None
    job_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._seq = 0
        self._recent: deque[Event] = deque(maxlen=REPLAY_SIZE)
        self.epoch = uuid.uuid4().hex  # changes when the node restarts

    def publish(self, type_: str, payload: dict[str, Any], *, job_id: str | None = None) -> Event:
        self._seq += 1
        event = Event(
            seq=self._seq,
            type=type_,
            payload=payload,
            job_id=job_id,
            correlation_id=get_correlation_id(),
        )
        self._recent.append(event)
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
    def last_seq(self) -> int:
        return self._seq

    def since(self, seq: int) -> list[Event] | None:
        """Events after ``seq``; ``None`` if some of them are no longer kept."""
        if seq >= self._seq:
            return []
        if not self._recent or self._recent[0].seq > seq + 1:
            return None
        return [e for e in self._recent if e.seq > seq]

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
