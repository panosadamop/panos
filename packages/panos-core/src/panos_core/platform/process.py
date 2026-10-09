"""Process groups whose whole tree can be killed (ADR-007). Dispatches per OS."""

import asyncio
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol


class ProcessGroup(Protocol):
    process: asyncio.subprocess.Process

    def kill_tree(self) -> None: ...
    def close(self) -> None: ...


async def spawn_group(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str]) -> ProcessGroup:
    """Start ``argv`` (no shell) in a new process group / Windows job object."""
    if sys.platform == "win32":
        from panos_core.platform.windows.process import spawn
    else:
        from panos_core.platform.posix.process import spawn
    return await spawn(argv, cwd=cwd, env=env)
