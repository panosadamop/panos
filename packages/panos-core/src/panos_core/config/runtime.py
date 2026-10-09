"""Node runtime file: how the CLI and desktop discover the running node (ADR-002/ADR-003).

The file holds the port and the per-start bearer token. It is created owner-only
*before* the token is written, so the token never exists in a wider-readable file.
"""

import json
import os
import secrets
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from panos_core.domain.clock import utc_now
from panos_core.platform.secure_file import create_owner_only_file

TOKEN_BYTES = 32


class RuntimeInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pid: int
    host: str
    port: int
    token: str
    version: str
    started_at: datetime

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def write_runtime_file(
    path: Path, *, host: str, port: int, token: str, version: str
) -> RuntimeInfo:
    info = RuntimeInfo(
        pid=os.getpid(), host=host, port=port, token=token, version=version, started_at=utc_now()
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    create_owner_only_file(path)
    path.write_text(info.model_dump_json(), encoding="utf-8")
    return info


def read_runtime_file(path: Path) -> RuntimeInfo | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        return RuntimeInfo.model_validate(json.loads(raw))
    except (ValueError, TypeError):
        return None


def remove_runtime_file(path: Path, *, expected_pid: int | None = None) -> None:
    """Delete the runtime file, optionally only if it belongs to ``expected_pid``."""
    if expected_pid is not None:
        info = read_runtime_file(path)
        if info is not None and info.pid != expected_pid:
            return
    path.unlink(missing_ok=True)
