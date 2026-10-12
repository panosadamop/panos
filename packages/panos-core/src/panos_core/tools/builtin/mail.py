"""Email tools: search, read and draft (FR-017, BR-019, US-043).

There is no ``email.send`` tool and the registry refuses to register one (AC-US043-02).
Drafts land in the user's Drafts folder; the user sends them from their mail client.
Message content is untrusted data (ADR-023): it is returned to the caller, never executed,
and carries the account's privacy label.
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from panos_core.connectors.gmail import GmailClient, MailMessage
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
            if account.kind != ConnectorKind.GOOGLE or "mail" not in account.scopes:
                raise PlanRejectedError(f"{account.label} is not a connected mail account")
            return account
        return await connectors.first(ConnectorKind.GOOGLE, "mail")
    except ConnectorError as exc:
        raise PlanRejectedError(str(exc)) from exc


class MailOut(_Model):
    id: str
    thread_id: str
    sender: str
    to: str
    subject: str
    date: str
    snippet: str = ""
    body: str = ""
    attachments: list[str] = []


# ===================================================================== email.search
class EmailSearchInput(_Model):
    query: str = Field(default="in:inbox", max_length=500, description="Gmail search syntax")
    limit: int = Field(default=10, ge=1, le=50)
    account: str | None = None


class EmailListOutput(_Model):
    account: str
    privacy_mode: str
    untrusted: bool = True
    messages: list[MailOut]


class EmailSearchTool(Tool[EmailSearchInput, EmailListOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="email.search",
        version="1",
        description="Search the connected mailbox (Gmail query syntax).",
        input_model=EmailSearchInput,
        output_model=EmailListOutput,
        capabilities=frozenset({"email.read"}),
        side_effect=SideEffect.NONE,
        timeout_s=60,
        cancellable=False,
        idempotent=True,
        privacy="Returns message headers and snippets with the account's privacy label.",
    )

    def __init__(self, connectors: ConnectorService) -> None:
        self._connectors = connectors

    async def plan(self, inp: EmailSearchInput, ctx: ToolContext) -> ActionPlan:
        account = await _account(self._connectors, inp.account)
        return ActionPlan(
            action="email.read",
            target=account.id,
            summary=f"Search {account.label} for {inp.query!r}",
            payload={"query": inp.query, "limit": inp.limit},
        )

    async def _execute(
        self, inp: EmailSearchInput, ctx: ToolContext, plan: ActionPlan
    ) -> EmailListOutput:
        account = await self._connectors.get(plan.target)
        http = self._connectors.client(account)
        try:
            found = await GmailClient(http).search(inp.query, limit=inp.limit)
        except ConnectorHttpError as exc:
            raise ToolExecutionError(str(exc)) from exc
        finally:
            await http.aclose()
        await self._connectors.touch(account)
        return EmailListOutput(
            account=account.label,
            privacy_mode=account.privacy_mode.value,
            messages=[
                MailOut(
                    id=m.id,
                    thread_id=m.thread_id,
                    sender=m.sender,
                    to=m.to,
                    subject=m.subject,
                    date=m.date,
                    snippet=m.snippet,
                )
                for m in found
            ],
        )


# ===================================================================== email.read
class EmailReadInput(_Model):
    message_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    thread_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    account: str | None = None

    @model_validator(mode="after")
    def _one(self) -> "EmailReadInput":
        if (self.message_id is None) == (self.thread_id is None):
            raise ValueError("give exactly one of message_id or thread_id")
        return self


def _out(m: MailMessage) -> MailOut:
    return MailOut(
        id=m.id,
        thread_id=m.thread_id,
        sender=m.sender,
        to=m.to,
        subject=m.subject,
        date=m.date,
        body=m.body,
        attachments=m.attachments,
    )


class EmailReadTool(Tool[EmailReadInput, EmailListOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="email.read",
        version="1",
        description="Read one message or a whole thread (text body, attachment names).",
        input_model=EmailReadInput,
        output_model=EmailListOutput,
        capabilities=frozenset({"email.read"}),
        side_effect=SideEffect.NONE,
        timeout_s=60,
        cancellable=False,
        idempotent=True,
        privacy="Returns message bodies with the account's privacy label; untrusted content.",
    )

    def __init__(self, connectors: ConnectorService) -> None:
        self._connectors = connectors

    async def plan(self, inp: EmailReadInput, ctx: ToolContext) -> ActionPlan:
        account = await _account(self._connectors, inp.account)
        what = f"message {inp.message_id}" if inp.message_id else f"thread {inp.thread_id}"
        return ActionPlan(
            action="email.read",
            target=account.id,
            summary=f"Read {what} in {account.label}",
            payload={"message_id": inp.message_id, "thread_id": inp.thread_id},
        )

    async def _execute(
        self, inp: EmailReadInput, ctx: ToolContext, plan: ActionPlan
    ) -> EmailListOutput:
        account = await self._connectors.get(plan.target)
        http = self._connectors.client(account)
        gmail = GmailClient(http)
        try:
            if inp.message_id:
                messages = [await gmail.message(inp.message_id)]
            else:
                messages = await gmail.thread(inp.thread_id or "")
        except ConnectorHttpError as exc:
            raise ToolExecutionError(str(exc)) from exc
        finally:
            await http.aclose()
        await self._connectors.touch(account)
        return EmailListOutput(
            account=account.label,
            privacy_mode=account.privacy_mode.value,
            messages=[_out(m) for m in messages],
        )


# ===================================================================== email.draft
class EmailDraftInput(_Model):
    to: list[EmailStr] = Field(min_length=1, max_length=50)
    cc: list[EmailStr] = Field(default_factory=list, max_length=50)
    subject: str = Field(max_length=500)
    body: str = Field(max_length=50_000)
    reply_to_message_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    draft_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    account: str | None = None


class EmailDraftOutput(_Model):
    account: str
    draft_id: str
    thread_id: str | None
    note: str = "Saved as a draft. Panos never sends email: review and send it yourself."


class EmailDraftTool(Tool[EmailDraftInput, EmailDraftOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="email.draft",
        version="1",
        description="Create or update a draft (never sends).",
        input_model=EmailDraftInput,
        output_model=EmailDraftOutput,
        capabilities=frozenset({"email.draft"}),
        side_effect=SideEffect.EXTERNAL,
        timeout_s=60,
        cancellable=False,
        idempotent=False,
        privacy="Writes a draft to the mailbox's Drafts folder only.",
    )

    def __init__(self, connectors: ConnectorService) -> None:
        self._connectors = connectors

    async def plan(self, inp: EmailDraftInput, ctx: ToolContext) -> ActionPlan:
        account = await _account(self._connectors, inp.account)
        return ActionPlan(
            action="email.draft",
            target=account.id,
            summary=f"Save a draft to {', '.join(inp.to)}: {inp.subject[:80]!r}",
            payload={
                "to": list(inp.to),
                "cc": list(inp.cc),
                "subject": inp.subject,
                "body": inp.body,
                "reply_to_message_id": inp.reply_to_message_id,
                "draft_id": inp.draft_id,
            },
        )

    async def _execute(
        self, inp: EmailDraftInput, ctx: ToolContext, plan: ActionPlan
    ) -> EmailDraftOutput:
        account = await self._connectors.get(plan.target)
        http = self._connectors.client(account)
        gmail = GmailClient(http)
        p = plan.payload
        try:
            original = (
                await gmail.message(p["reply_to_message_id"]) if p["reply_to_message_id"] else None
            )
            draft = await gmail.save_draft(
                to=p["to"],
                cc=p["cc"],
                subject=p["subject"],
                body=p["body"],
                reply_to=original,
                draft_id=p["draft_id"],
            )
        except ConnectorHttpError as exc:
            raise ToolExecutionError(str(exc)) from exc
        finally:
            await http.aclose()
        await self._connectors.touch(account)
        return EmailDraftOutput(account=account.label, draft_id=draft.id, thread_id=draft.thread_id)


MAIL_TOOLS = (EmailSearchTool, EmailReadTool, EmailDraftTool)
