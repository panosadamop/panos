"""Canonical JSON (RFC 8785 / JCS) and hashing, used for approval binding and the audit chain."""

import hashlib
from typing import Any

import rfc8785
from pydantic_core import to_jsonable_python


def canonical_bytes(value: Any) -> bytes:
    """Serialize ``value`` deterministically. Datetimes, enums, paths etc. become JSON first."""
    result: bytes = rfc8785.dumps(to_jsonable_python(value))
    return result


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_hash(value: Any) -> str:
    """SHA-256 hex digest of the canonical JSON form of ``value``."""
    return sha256_hex(canonical_bytes(value))
