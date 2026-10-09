"""Correlation, job and step IDs carried through async code via contextvars."""

import re
import uuid
from typing import Final

import structlog

_CORRELATION_RE: Final = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")
CONTEXT_KEYS: Final = ("correlation_id", "job_id", "step_id", "project_id")


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def sanitize_correlation_id(value: str | None) -> str:
    """Accept a client-provided correlation ID only if it is well-formed."""
    if value and _CORRELATION_RE.match(value):
        return value
    return new_correlation_id()


def bind_context(**ids: str | None) -> None:
    """Bind known context IDs for all log events in the current context."""
    unknown = set(ids) - set(CONTEXT_KEYS)
    if unknown:
        raise ValueError(f"unknown context keys: {sorted(unknown)}")
    structlog.contextvars.bind_contextvars(**{k: v for k, v in ids.items() if v is not None})


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


def get_correlation_id() -> str | None:
    value = structlog.contextvars.get_contextvars().get("correlation_id")
    return value if isinstance(value, str) else None
