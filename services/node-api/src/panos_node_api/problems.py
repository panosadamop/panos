"""RFC 9457 problem details (``application/problem+json``)."""

import json
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from panos_core.observability import get_correlation_id, get_logger

PROBLEM_CONTENT_TYPE = "application/problem+json"

log = get_logger(__name__)


def problem_body(status: int, detail: str | None = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
    }
    if detail:
        body["detail"] = detail
    correlation_id = get_correlation_id()
    if correlation_id:
        body["correlation_id"] = correlation_id
    body.update(extra)
    return body


def problem_response(status: int, detail: str | None = None, **extra: Any) -> JSONResponse:
    return JSONResponse(
        problem_body(status, detail, **extra), status_code=status, media_type=PROBLEM_CONTENT_TYPE
    )


def problem_bytes(status: int, detail: str) -> bytes:
    return json.dumps(problem_body(status, detail)).encode()


async def _http_exception(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    detail = exc.detail if isinstance(exc.detail, str) else None
    return problem_response(exc.status_code, detail)


async def _validation_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    errors = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in exc.errors()
    ]
    return problem_response(422, "request validation failed", errors=errors)


async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
    # Never leak internals to the client; full details go to the (redacted) log.
    log.exception("unhandled_error", error_type=type(exc).__name__)
    return problem_response(500, "internal error")


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled)
