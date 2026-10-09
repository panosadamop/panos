"""Entity identifiers.

IDs are ``<prefix>_<32 hex chars>`` so their type is obvious in logs and audit records.
"""

import re
import uuid
from typing import Final

PREFIXES: Final = {
    "project": "prj",
    "job": "job",
    "step": "stp",
    "approval": "apr",
    "audit": "aud",
    "node": "nod",
    "checkpoint": "chk",
    "snapshot": "snp",
    "application": "app",
    "preference": "prf",
    "conversation": "cnv",
    "message": "msg",
    "usage": "use",
    "requirement": "req",
    "requirement_approval": "rap",
    "trace_link": "trl",
    "interview": "itv",
    "skill": "skl",
}

_ID_RE: Final = re.compile(r"^[a-z]{3}_[0-9a-f]{32}$")


def new_id(kind: str) -> str:
    """Return a new random identifier for an entity kind listed in ``PREFIXES``."""
    try:
        prefix = PREFIXES[kind]
    except KeyError:
        raise ValueError(f"unknown entity kind: {kind!r}") from None
    return f"{prefix}_{uuid.uuid4().hex}"


def is_valid_id(value: str, kind: str | None = None) -> bool:
    """Check the ID format and, optionally, that it belongs to ``kind``."""
    if not _ID_RE.match(value):
        return False
    return kind is None or value.startswith(PREFIXES[kind] + "_")
