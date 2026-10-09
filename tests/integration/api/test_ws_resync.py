"""/ws/events reconnect: replay of missed events or an explicit resync (M7-04)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from panos_core.events import REPLAY_SIZE, EventBus
from tests.integration.api.conftest import PORT, TOKEN

pytestmark = pytest.mark.req("FR-022")

WS = f"ws://127.0.0.1:{PORT}/api/v1/ws/events"


def bus(client: TestClient) -> EventBus:
    events: EventBus = client.app.state.container.events  # type: ignore[attr-defined]
    return events


def connect(client: TestClient, **auth: Any) -> Any:
    ws = client.websocket_connect(WS)
    sock = ws.__enter__()
    sock.send_json({"type": "auth", "token": TOKEN, **auth})
    return ws, sock, sock.receive_json()


def test_events_have_sequence_numbers_and_missed_ones_are_replayed(client: TestClient) -> None:
    b = bus(client)
    first = b.publish("test.one", {"n": 1})
    ws, sock, ok = connect(client)
    try:
        assert ok["seq"] == first.seq and ok["epoch"] == b.epoch
        b.publish("test.two", {"n": 2})
        live = sock.receive_json()
        assert live["type"] == "test.two" and live["seq"] == first.seq + 1
    finally:
        ws.__exit__(None, None, None)
    # Offline for a while: three events happen.
    for n in (3, 4, 5):
        b.publish("test.missed", {"n": n})
    ws, sock, ok = connect(client, since=first.seq + 1, epoch=b.epoch)
    try:
        replayed = [sock.receive_json() for _ in range(3)]
        assert [e["payload"]["n"] for e in replayed] == [3, 4, 5]
        assert [e["seq"] for e in replayed] == list(range(first.seq + 2, first.seq + 5))
        b.publish("test.after", {"n": 6})
        assert sock.receive_json()["payload"]["n"] == 6  # then live, no duplicates
    finally:
        ws.__exit__(None, None, None)


def test_too_old_or_restarted_means_resync(client: TestClient) -> None:
    b = bus(client)
    start = b.publish("test.start", {}).seq
    for i in range(REPLAY_SIZE + 5):
        b.publish("test.flood", {"i": i})
    ws, sock, _ = connect(client, since=start, epoch=b.epoch)
    try:
        assert sock.receive_json()["type"] == "resync"
    finally:
        ws.__exit__(None, None, None)
    ws, sock, _ = connect(client, since=0, epoch="an-older-node")
    try:
        assert sock.receive_json()["type"] == "resync"
    finally:
        ws.__exit__(None, None, None)
    ws, sock, _ = connect(client, since=b.last_seq, epoch=b.epoch)
    try:  # up to date: nothing replayed, nothing to resync
        b.publish("test.next", {})
        assert sock.receive_json()["type"] == "test.next"
    finally:
        ws.__exit__(None, None, None)


def test_event_bus_since() -> None:
    b = EventBus()
    assert b.since(0) == [] and b.last_seq == 0
    e1, e2 = b.publish("a", {}), b.publish("b", {})
    assert b.since(0) == [e1, e2] and b.since(1) == [e2] and b.since(2) == []
