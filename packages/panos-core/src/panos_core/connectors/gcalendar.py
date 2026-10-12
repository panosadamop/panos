"""Google Calendar: read, free/busy, conflicts and approved changes (FR-018, BR-020, M8-02)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final
from urllib.parse import quote

from panos_core.connectors.http import ConnectorHttp

API: Final = "https://www.googleapis.com/calendar/v3"


@dataclass(frozen=True)
class Event:
    id: str
    calendar_id: str
    summary: str
    start: str  # RFC 3339, or a date for all-day events
    end: str
    all_day: bool
    status: str
    location: str = ""
    organizer: str = ""
    attendees: list[dict[str, Any]] = field(default_factory=list)
    my_response: str | None = None  # accepted | declined | tentative | needsAction
    transparent: bool = False  # "free" time, never a conflict
    html_link: str = ""


@dataclass(frozen=True)
class Conflict:
    first: str  # event ids
    second: str
    overlap_start: str
    overlap_end: str


def _event(data: dict[str, Any], calendar_id: str) -> Event:
    start, end = data.get("start", {}), data.get("end", {})
    me = next((a for a in data.get("attendees", []) if a.get("self")), None)
    return Event(
        id=data["id"],
        calendar_id=calendar_id,
        summary=data.get("summary", "(no title)"),
        start=start.get("dateTime") or start.get("date", ""),
        end=end.get("dateTime") or end.get("date", ""),
        all_day="date" in start,
        status=data.get("status", "confirmed"),
        location=data.get("location", ""),
        organizer=(data.get("organizer") or {}).get("email", ""),
        attendees=[
            {"email": a.get("email", ""), "response": a.get("responseStatus", "")}
            for a in data.get("attendees", [])
        ],
        my_response=me.get("responseStatus") if me else None,
        transparent=data.get("transparency") == "transparent",
        html_link=data.get("htmlLink", ""),
    )


def _blocks_time(e: Event) -> bool:
    return not (
        e.all_day or e.transparent or e.status == "cancelled" or e.my_response == "declined"
    )


def conflicts(events: list[Event]) -> list[Conflict]:
    """Pairs of overlapping timed events that block time (AC-US044-01)."""
    timed = sorted(
        (
            (datetime.fromisoformat(e.start), datetime.fromisoformat(e.end), e)
            for e in events
            if _blocks_time(e)
        ),
        key=lambda t: t[0],
    )
    out = []
    for i, (s1, e1, a) in enumerate(timed):
        for s2, e2, b in timed[i + 1 :]:
            if s2 >= e1:
                break
            out.append(Conflict(a.id, b.id, max(s1, s2).isoformat(), min(e1, e2).isoformat()))
    return out


class CalendarClient:
    def __init__(self, http: ConnectorHttp) -> None:
        self._http = http

    async def calendars(self) -> list[dict[str, Any]]:
        data = await self._http.request("GET", f"{API}/users/me/calendarList")
        return [
            {"id": c["id"], "summary": c.get("summary", ""), "primary": bool(c.get("primary"))}
            for c in (data or {}).get("items", [])
        ]

    async def events(
        self, time_min: str, time_max: str, *, calendar_id: str = "primary", limit: int = 250
    ) -> list[Event]:
        data = await self._http.request(
            "GET",
            f"{API}/calendars/{quote(calendar_id, safe='@.')}/events",
            params={
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": min(limit, 2500),
            },
        )
        return [_event(item, calendar_id) for item in (data or {}).get("items", [])]

    async def raw_event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        data = await self._http.request(
            "GET", f"{API}/calendars/{quote(calendar_id, safe='@.')}/events/{quote(event_id)}"
        )
        return data if isinstance(data, dict) else {}

    async def event(self, calendar_id: str, event_id: str) -> Event:
        return _event(await self.raw_event(calendar_id, event_id), calendar_id)

    async def freebusy(
        self, time_min: str, time_max: str, calendars: list[str]
    ) -> dict[str, list[dict[str, str]]]:
        data = await self._http.request(
            "POST",
            f"{API}/freeBusy",
            json={
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": c} for c in calendars],
            },
        )
        return {
            cal: [{"start": b["start"], "end": b["end"]} for b in info.get("busy", [])]
            for cal, info in (data or {}).get("calendars", {}).items()
        }

    async def apply(self, operation: dict[str, Any]) -> dict[str, Any] | None:
        """Execute exactly one prepared operation (already approved by the user)."""
        cal = quote(operation["calendar_id"], safe="@.")
        kind = operation["operation"]
        params = {"sendUpdates": operation.get("send_updates", "none")}
        if kind == "create":
            result = await self._http.request(
                "POST", f"{API}/calendars/{cal}/events", params=params, json=operation["body"]
            )
        elif kind in ("update", "respond"):
            result = await self._http.request(
                "PATCH",
                f"{API}/calendars/{cal}/events/{quote(operation['event_id'])}",
                params=params,
                json=operation["body"],
            )
        elif kind == "delete":
            result = await self._http.request(
                "DELETE",
                f"{API}/calendars/{cal}/events/{quote(operation['event_id'])}",
                params=params,
            )
        else:
            raise ValueError(f"unknown calendar operation {kind!r}")
        return result if isinstance(result, dict) else None
