"""Calendar tools (FR-018, BR-020, US-044).

* ``calendar.events`` and ``calendar.freebusy`` read (P0) and report conflicts.
* ``calendar.change`` prepares one create/update/delete/respond operation. Its plan payload
  is the exact request that will be sent, so the approval binds to it (P3, AC-US044-02); if
  anything differs at execution time the approval does not match and nothing is written.
  Attendees are not notified unless the operation says so (``send_updates``), and that
  choice is part of what the user approves.
"""

from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr, Field, model_validator

from panos_core.connectors.gcalendar import CalendarClient, Event, conflicts
from panos_core.connectors.http import ConnectorHttpError
from panos_core.connectors.service import ConnectorError, ConnectorService
from panos_core.domain.connectors import ConnectorAccount, ConnectorKind
from panos_core.tools.contract import (
    ActionPlan,
    PlanRejectedError,
    SideEffect,
    Tool,
    ToolContext,
    ToolExecutionError,
    ToolSpec,
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _account(connectors: ConnectorService, ref: str | None) -> ConnectorAccount:
    try:
        if ref:
            account = await connectors.get(ref)
            if account.kind != ConnectorKind.GOOGLE or "calendar" not in account.scopes:
                raise PlanRejectedError(f"{account.label} is not a connected calendar account")
            return account
        return await connectors.first(ConnectorKind.GOOGLE, "calendar")
    except ConnectorError as exc:
        raise PlanRejectedError(str(exc)) from exc


class EventOut(_Model):
    id: str
    calendar_id: str
    summary: str
    start: str
    end: str
    all_day: bool
    status: str
    location: str
    organizer: str
    my_response: str | None
    attendees: list[dict[str, Any]]


class ConflictOut(_Model):
    first: str
    second: str
    overlap_start: str
    overlap_end: str


def _event_out(e: Event) -> EventOut:
    return EventOut(
        id=e.id,
        calendar_id=e.calendar_id,
        summary=e.summary,
        start=e.start,
        end=e.end,
        all_day=e.all_day,
        status=e.status,
        location=e.location,
        organizer=e.organizer,
        my_response=e.my_response,
        attendees=e.attendees,
    )


# ===================================================================== calendar.events
class CalendarEventsInput(_Model):
    start: AwareDatetime
    end: AwareDatetime
    calendar_id: str = Field(default="primary", max_length=256)
    account: str | None = None

    @model_validator(mode="after")
    def _range(self) -> "CalendarEventsInput":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if (self.end - self.start).days > 92:
            raise ValueError("ask for at most 92 days at a time")
        return self


class CalendarEventsOutput(_Model):
    account: str
    privacy_mode: str
    untrusted: bool = True
    events: list[EventOut]
    conflicts: list[ConflictOut]


class CalendarEventsTool(Tool[CalendarEventsInput, CalendarEventsOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="calendar.events",
        version="1",
        description="List calendar events in a time range and the conflicts between them.",
        input_model=CalendarEventsInput,
        output_model=CalendarEventsOutput,
        capabilities=frozenset({"calendar.read"}),
        side_effect=SideEffect.NONE,
        timeout_s=60,
        cancellable=False,
        idempotent=True,
        privacy="Returns event details with the account's privacy label.",
    )

    def __init__(self, connectors: ConnectorService) -> None:
        self._connectors = connectors

    async def plan(self, inp: CalendarEventsInput, ctx: ToolContext) -> ActionPlan:
        account = await _account(self._connectors, inp.account)
        return ActionPlan(
            action="calendar.read",
            target=account.id,
            summary=(
                f"Read {inp.calendar_id} events "
                f"{inp.start:%Y-%m-%d %H:%M} – {inp.end:%Y-%m-%d %H:%M}"
            ),
            payload={"calendar_id": inp.calendar_id},
        )

    async def _execute(
        self, inp: CalendarEventsInput, ctx: ToolContext, plan: ActionPlan
    ) -> CalendarEventsOutput:
        account = await self._connectors.get(plan.target)
        http = self._connectors.client(account)
        try:
            events = await CalendarClient(http).events(
                inp.start.isoformat(), inp.end.isoformat(), calendar_id=inp.calendar_id
            )
        except ConnectorHttpError as exc:
            raise ToolExecutionError(str(exc)) from exc
        finally:
            await http.aclose()
        await self._connectors.touch(account)
        return CalendarEventsOutput(
            account=account.label,
            privacy_mode=account.privacy_mode.value,
            events=[_event_out(e) for e in events],
            conflicts=[ConflictOut(**c.__dict__) for c in conflicts(events)],
        )


# ===================================================================== calendar.freebusy
class FreeBusyInput(_Model):
    start: AwareDatetime
    end: AwareDatetime
    calendars: list[str] = Field(default_factory=lambda: ["primary"], min_length=1, max_length=20)
    account: str | None = None


class FreeBusyOutput(_Model):
    account: str
    busy: dict[str, list[dict[str, str]]]


class CalendarFreeBusyTool(Tool[FreeBusyInput, FreeBusyOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="calendar.freebusy",
        version="1",
        description="Busy intervals for one or more calendars (own or shared with you).",
        input_model=FreeBusyInput,
        output_model=FreeBusyOutput,
        capabilities=frozenset({"calendar.read"}),
        side_effect=SideEffect.NONE,
        timeout_s=60,
        cancellable=False,
        idempotent=True,
        privacy="Returns busy intervals only (no event details).",
    )

    def __init__(self, connectors: ConnectorService) -> None:
        self._connectors = connectors

    async def plan(self, inp: FreeBusyInput, ctx: ToolContext) -> ActionPlan:
        account = await _account(self._connectors, inp.account)
        return ActionPlan(
            action="calendar.read",
            target=account.id,
            summary=f"Free/busy for {', '.join(inp.calendars)}",
            payload={"calendars": inp.calendars},
        )

    async def _execute(
        self, inp: FreeBusyInput, ctx: ToolContext, plan: ActionPlan
    ) -> FreeBusyOutput:
        account = await self._connectors.get(plan.target)
        http = self._connectors.client(account)
        try:
            busy = await CalendarClient(http).freebusy(
                inp.start.isoformat(), inp.end.isoformat(), inp.calendars
            )
        except ConnectorHttpError as exc:
            raise ToolExecutionError(str(exc)) from exc
        finally:
            await http.aclose()
        return FreeBusyOutput(account=account.label, busy=busy)


# ===================================================================== calendar.change
class CalendarChangeInput(_Model):
    operation: Literal["create", "update", "delete", "respond"]
    calendar_id: str = Field(default="primary", max_length=256)
    event_id: str | None = Field(default=None, max_length=1024)
    summary: str | None = Field(default=None, max_length=500)
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    location: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=8000)
    attendees: list[EmailStr] | None = Field(default=None, max_length=100)
    response: Literal["accepted", "declined", "tentative"] | None = None
    send_updates: Literal["none", "all", "externalOnly"] = "none"
    account: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> "CalendarChangeInput":
        if self.operation != "create" and not self.event_id:
            raise ValueError(f"{self.operation} needs event_id")
        if self.operation == "create" and not (self.summary and self.start and self.end):
            raise ValueError("create needs summary, start and end")
        if self.operation == "respond" and self.response is None:
            raise ValueError("respond needs response")
        if self.start and self.end and self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class CalendarChangeOutput(_Model):
    account: str
    operation: str
    event_id: str | None
    html_link: str | None = None


def _time(dt: datetime) -> dict[str, str]:
    return {"dateTime": dt.isoformat()}


class CalendarChangeTool(Tool[CalendarChangeInput, CalendarChangeOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="calendar.change",
        version="1",
        description=(
            "Create, update, delete or respond to one event. Requires approval of the exact "
            "operation."
        ),
        input_model=CalendarChangeInput,
        output_model=CalendarChangeOutput,
        capabilities=frozenset({"calendar.write"}),
        side_effect=SideEffect.EXTERNAL,
        timeout_s=60,
        cancellable=False,
        idempotent=False,
        privacy="Writes one event to the user's calendar after approval.",
    )

    def __init__(self, connectors: ConnectorService) -> None:
        self._connectors = connectors

    async def plan(self, inp: CalendarChangeInput, ctx: ToolContext) -> ActionPlan:
        account = await _account(self._connectors, inp.account)
        body: dict[str, Any] = {}
        current: dict[str, Any] = {}
        if inp.operation in ("update", "delete", "respond"):
            http = self._connectors.client(account)
            try:
                current = await CalendarClient(http).raw_event(inp.calendar_id, inp.event_id or "")
            except ConnectorHttpError as exc:
                raise PlanRejectedError(f"cannot read the event: {exc}") from exc
            finally:
                await http.aclose()
        if inp.operation in ("create", "update"):
            if inp.summary is not None:
                body["summary"] = inp.summary
            if inp.start is not None:
                body["start"] = _time(inp.start)
            if inp.end is not None:
                body["end"] = _time(inp.end)
            if inp.location is not None:
                body["location"] = inp.location
            if inp.description is not None:
                body["description"] = inp.description
            if inp.attendees is not None:
                body["attendees"] = [{"email": a} for a in inp.attendees]
            if inp.operation == "update" and not body:
                raise PlanRejectedError("nothing to change")
        elif inp.operation == "respond":
            attendees = current.get("attendees", [])
            if not any(a.get("self") for a in attendees):
                raise PlanRejectedError("you are not an attendee of this event")
            body["attendees"] = [
                {**a, "responseStatus": inp.response} if a.get("self") else a for a in attendees
            ]
        title = current.get("summary") or inp.summary or inp.event_id
        when = (current.get("start") or {}).get("dateTime") or (
            inp.start.isoformat() if inp.start else ""
        )
        verb = {
            "create": "Create",
            "update": "Change",
            "delete": "Delete",
            "respond": f"Reply {inp.response} to",
        }[inp.operation]
        notify = "" if inp.send_updates == "none" else f" and notify attendees ({inp.send_updates})"
        return ActionPlan(
            action="calendar.write",
            target=f"{account.id}:{inp.calendar_id}",
            summary=f"{verb} event {title!r} {when}{notify}".strip(),
            payload={
                "account": account.id,
                "operation": inp.operation,
                "calendar_id": inp.calendar_id,
                "event_id": inp.event_id,
                "body": body,
                "send_updates": inp.send_updates,
            },
        )

    async def _execute(
        self, inp: CalendarChangeInput, ctx: ToolContext, plan: ActionPlan
    ) -> CalendarChangeOutput:
        account = await self._connectors.get(plan.payload["account"])
        http = self._connectors.client(account)
        try:
            result = await CalendarClient(http).apply(plan.payload)
        except ConnectorHttpError as exc:
            raise ToolExecutionError(str(exc)) from exc
        finally:
            await http.aclose()
        await self._connectors.touch(account)
        return CalendarChangeOutput(
            account=account.label,
            operation=plan.payload["operation"],
            event_id=(result or {}).get("id") or plan.payload["event_id"],
            html_link=(result or {}).get("htmlLink"),
        )


CALENDAR_TOOLS = (CalendarEventsTool, CalendarFreeBusyTool, CalendarChangeTool)
