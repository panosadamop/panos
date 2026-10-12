"""Shared router dependencies and helpers."""

from typing import Annotated, NoReturn

from fastapi import Depends, HTTPException, Request

from panos_core.tools import ToolResult
from panos_node_api.container import NodeContainer
from panos_node_api.schemas import ToolResultView

# The loopback API authenticates the local user session (ADR-003); actions are theirs.
LOCAL_USER = "user"

_REASON_STATUS = {
    "not_found": 404,
    "conflict": 409,
    "upstream": 502,
    "duplicate": 409,
    "not_pending": 409,
    "expired": 409,
    "not_installed": 404,
    "unsupported": 400,
    "no_ide": 404,
    "project_mismatch": 409,
    "empty": 400,
    "invalid_state": 409,
    "mismatch": 409,
    "git_failed": 500,
    "invalid": 400,
    "invalid_parent": 400,
    "not_approved": 409,
}


def get_container(request: Request) -> NodeContainer:
    container: NodeContainer = request.app.state.container
    return container


Container = Annotated[NodeContainer, Depends(get_container)]


def raise_for_reason(reason: str, message: str) -> NoReturn:
    """Map a domain error reason code to an HTTP problem response."""
    raise HTTPException(status_code=_REASON_STATUS.get(reason, 400), detail=message)


def tool_result_view(result: ToolResult) -> ToolResultView:
    return ToolResultView(
        tool=result.tool,
        status=result.status,
        action=result.action,
        output=result.output,
        error=result.error,
        approval_id=result.approval_id,
    )
