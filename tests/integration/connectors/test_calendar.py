"""Calendar tools (M8-02, FR-018, BR-020, US-044): conflicts, free/busy and approved writes."""

import pytest

from panos_core.connectors.gcalendar import _event as parse_event
from panos_core.connectors.gcalendar import conflicts
from panos_core.tools import ToolStatus
from tests.connector_fakes import body, calendar_event, install_calendar, query
from tests.integration.connectors.conftest import Env, connect_google

pytestmark = pytest.mark.req("FR-018", "BR-020", "US-044")

DAY = {"start": "2026-10-12T00:00:00+03:00", "end": "2026-10-13T00:00:00+03:00"}


@pytest.fixture
async def calendar(env: Env) -> Env:
    install_calendar(env.cloud)
    ev = env.cloud.events
    ev["a"] = calendar_event(
        "a", "Standup", "2026-10-12T09:00:00+03:00", "2026-10-12T09:30:00+03:00"
    )
    ev["b"] = calendar_event(
        "b", "Client call", "2026-10-12T09:15:00+03:00", "2026-10-12T10:00:00+03:00"
    )
    ev["c"] = calendar_event(
        "c",
        "Declined sync",
        "2026-10-12T09:00:00+03:00",
        "2026-10-12T10:00:00+03:00",
        me="declined",
    )
    ev["d"] = calendar_event(
        "d",
        "Focus (free)",
        "2026-10-12T09:00:00+03:00",
        "2026-10-12T12:00:00+03:00",
        transparent=True,
    )
    ev["e"] = calendar_event("e", "Holiday", "2026-10-12", "2026-10-13", all_day=True)
    await connect_google(env, ["calendar"])
    return env


@pytest.mark.req("AC-US044-01")
async def test_events_report_only_real_conflicts(calendar: Env) -> None:
    env = calendar
    result = await env.runtime.invoke("calendar.events", DAY, env.ctx())
    assert result.status == ToolStatus.SUCCEEDED, result.error
    out = result.output or {}
    assert len(out["events"]) == 5 and out["privacy_mode"] == "LOCAL_ONLY"
    assert out["conflicts"] == [
        {
            "first": "a",
            "second": "b",
            "overlap_start": "2026-10-12T09:15:00+03:00",
            "overlap_end": "2026-10-12T09:30:00+03:00",
        }
    ]
    sent = query(env.cloud.calls("GET", "/events")[-1])
    assert sent["singleEvents"] == "true" and sent["timeMin"] == DAY["start"]

    env.cloud.busy = [{"start": "2026-10-12T06:00:00Z", "end": "2026-10-12T07:00:00Z"}]
    fb = await env.runtime.invoke(
        "calendar.freebusy", {**DAY, "calendars": ["primary", "maria@client.example"]}, env.ctx()
    )
    assert set((fb.output or {})["busy"]) == {"primary", "maria@client.example"}
    too_long = await env.runtime.invoke(
        "calendar.events", {"start": DAY["start"], "end": "2027-03-01T00:00:00+02:00"}, env.ctx()
    )
    assert too_long.status == ToolStatus.INVALID_INPUT


def test_back_to_back_meetings_are_not_conflicts() -> None:
    events = [
        parse_event(calendar_event("x", "A", "2026-10-12T09:00:00Z", "2026-10-12T10:00:00Z"), "p"),
        parse_event(calendar_event("y", "B", "2026-10-12T10:00:00Z", "2026-10-12T11:00:00Z"), "p"),
    ]
    assert conflicts(events) == []


@pytest.mark.req("AC-US044-02")
async def test_changes_need_approval_of_the_exact_operation(calendar: Env) -> None:
    env = calendar
    create = {
        "operation": "create",
        "summary": "Review with Maria",
        "start": "2026-10-13T11:00:00+03:00",
        "end": "2026-10-13T11:30:00+03:00",
        "attendees": ["maria@client.example"],
    }
    pending = await env.runtime.invoke("calendar.change", create, env.ctx())
    assert pending.status == ToolStatus.APPROVAL_REQUIRED and pending.approval_id
    assert env.cloud.calls("POST", "/events") == []  # nothing written yet
    decision = await env.approvals.decide(pending.approval_id, approve=True, decided_by="user")
    assert decision.token is not None

    changed = {**create, "summary": "Review with Maria (moved)"}
    tampered = await env.runtime.invoke(
        "calendar.change", changed, env.ctx(), approval_token=decision.token
    )
    assert tampered.status == ToolStatus.DENIED  # approval bound to the exact operation
    assert env.cloud.calls("POST", "/events") == []

    # A mismatched use invalidates the approval (M5): even the original now needs a new one.
    reused = await env.runtime.invoke(
        "calendar.change", create, env.ctx(), approval_token=decision.token
    )
    assert reused.status == ToolStatus.DENIED
    again = await env.runtime.invoke("calendar.change", create, env.ctx())
    assert again.approval_id
    decision = await env.approvals.decide(again.approval_id, approve=True, decided_by="user")
    done = await env.runtime.invoke(
        "calendar.change", create, env.ctx(), approval_token=decision.token
    )
    assert done.status == ToolStatus.SUCCEEDED, done.error
    assert len(env.cloud.calls("POST", "/events")) == 1
    posted = env.cloud.calls("POST", "/events")[-1]
    assert query(posted)["sendUpdates"] == "none"  # attendees not notified unless approved
    assert body(posted)["attendees"] == [{"email": "maria@client.example"}]


async def test_respond_keeps_other_attendees_and_delete_is_gated(calendar: Env) -> None:
    env = calendar
    respond = {"operation": "respond", "event_id": "b", "response": "declined"}
    pending = await env.runtime.invoke("calendar.change", respond, env.ctx())
    assert pending.status == ToolStatus.APPROVAL_REQUIRED and pending.approval_id
    decision = await env.approvals.decide(pending.approval_id, approve=True, decided_by="user")
    done = await env.runtime.invoke(
        "calendar.change", respond, env.ctx(), approval_token=decision.token
    )
    assert done.status == ToolStatus.SUCCEEDED, done.error
    attendees = env.cloud.events["b"]["attendees"]
    assert {a["email"]: a["responseStatus"] for a in attendees} == {
        "owner@example.com": "declined",
        "boss@client.example": "accepted",
    }

    delete = await env.runtime.invoke(
        "calendar.change", {"operation": "delete", "event_id": "a"}, env.ctx()
    )
    assert delete.status == ToolStatus.APPROVAL_REQUIRED
    assert "a" in env.cloud.events
    env.cloud.events["e"].pop("attendees")
    not_invited = await env.runtime.invoke(
        "calendar.change",
        {"operation": "respond", "event_id": "e", "response": "accepted"},
        env.ctx(),
    )
    assert not_invited.status == ToolStatus.DENIED
