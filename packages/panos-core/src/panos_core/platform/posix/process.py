"""POSIX process groups (new session; the whole group is signalled)."""

import asyncio
import contextlib
import os
import signal
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

assert sys.platform != "win32"  # noqa: S101 - imported only on POSIX by the dispatcher


class PosixProcessGroup:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process

    def kill_tree(self) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.process.pid, signal.SIGKILL)

    def close(self) -> None:
        return None


async def spawn(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str]) -> PosixProcessGroup:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=dict(env),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    return PosixProcessGroup(process)
