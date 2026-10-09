"""Conversation with Panos (FR-002, US-001/002): one turn per request, optional NDJSON stream."""

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from panos_core.ai.ports import ProviderError
from panos_core.ai.privacy import PrivacyViolationError
from panos_core.ai.router import NoRouteError
from panos_core.conversation import ConversationError, reply_payload
from panos_core.observability import get_logger
from panos_node_api.problems import problem_response
from panos_node_api.routers.deps import LOCAL_USER, Container, raise_for_reason

log = get_logger(__name__)
router = APIRouter(tags=["conversation"])

NDJSON = "application/x-ndjson"


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=20_000)
    conversation_id: str | None = None
    project: str | None = Field(default=None, description="project id or name")
    stream: bool = False


class ReplyView(BaseModel):
    conversation_id: str
    message_id: str
    text: str
    privacy: str
    intent: str
    provider: str | None
    model: str | None
    locality: str | None
    route_reasons: list[str]
    sources: list[str]
    tool: dict[str, Any] | None
    latency_ms: int


class MessageView(BaseModel):
    id: str
    role: str
    content: str
    privacy: str
    provider: str | None
    model: str | None
    created_at: str


class ConversationView(BaseModel):
    id: str
    project_id: str | None
    title: str
    privacy: str
    created_at: str
    updated_at: str
    messages: list[MessageView] = Field(default_factory=list)


def ai_error_response(exc: Exception) -> JSONResponse:
    """Explainable failures (AC-US005-04): privacy refusals are never silent."""
    if isinstance(exc, NoRouteError):
        return problem_response(
            503,
            exc.summary,
            reason="no_route",
            rejected=[{"candidate": k, "reason": r} for k, r in exc.rejected],
        )
    if isinstance(exc, PrivacyViolationError):
        return problem_response(
            403, "blocked: local-only content cannot leave this computer", reason="egress_blocked"
        )
    return problem_response(502, f"model provider failed: {exc}", reason="provider_error")


def _error_event(exc: Exception) -> dict[str, Any]:
    body = json.loads(bytes(ai_error_response(exc).body))
    return {"type": "error", "error": body}


@router.post(
    "/conversation/messages", response_model=ReplyView, responses={200: {"content": {NDJSON: {}}}}
)
async def send_message(body: MessageRequest, container: Container) -> Any:
    service = container.conversations
    if body.stream:
        events = service.stream(
            body.text,
            conversation_id=body.conversation_id,
            project_ref=body.project,
            actor=LOCAL_USER,
        )
        try:
            first = await anext(events)  # surface lookup errors as normal HTTP problems
        except ConversationError as exc:
            raise_for_reason(exc.reason, str(exc))

        async def lines() -> AsyncIterator[bytes]:
            yield (json.dumps(first) + "\n").encode()
            try:
                async for event in events:
                    yield (json.dumps(event, ensure_ascii=False) + "\n").encode()
            except (NoRouteError, PrivacyViolationError, ProviderError) as exc:
                yield (json.dumps(_error_event(exc)) + "\n").encode()

        return StreamingResponse(lines(), media_type=NDJSON)
    try:
        reply = await service.send(
            body.text,
            conversation_id=body.conversation_id,
            project_ref=body.project,
            actor=LOCAL_USER,
        )
    except ConversationError as exc:
        raise_for_reason(exc.reason, str(exc))
    except (NoRouteError, PrivacyViolationError, ProviderError) as exc:
        return ai_error_response(exc)
    return ReplyView(**reply_payload(reply))


@router.get("/conversations", response_model=list[ConversationView])
async def list_conversations(container: Container) -> list[ConversationView]:
    return [_view(c) for c in await container.conversations.list()]


@router.get("/conversations/{conversation_id}", response_model=ConversationView)
async def get_conversation(conversation_id: str, container: Container) -> ConversationView:
    try:
        conversation, messages = await container.conversations.get(conversation_id)
    except ConversationError as exc:
        raise_for_reason(exc.reason, str(exc))
    view = _view(conversation)
    view.messages = [
        MessageView(
            id=m.id,
            role=m.role,
            content=m.content,
            privacy=m.privacy.value,
            provider=m.provider,
            model=m.model,
            created_at=m.created_at.isoformat(),
        )
        for m in messages
    ]
    return view


def _view(c: Any) -> ConversationView:
    return ConversationView(
        id=c.id,
        project_id=c.project_id,
        title=c.title,
        privacy=c.privacy.value,
        created_at=c.created_at.isoformat(),
        updated_at=c.updated_at.isoformat(),
    )
