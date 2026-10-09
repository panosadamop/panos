"""Run a process with timeout, cancellation, process-tree kill and capped output."""

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from panos_core.platform.process import spawn_group
from panos_core.tools.contract import CancelToken

_CHUNK = 64 * 1024


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_s: float
    timed_out: bool
    cancelled: bool


async def _drain(stream: asyncio.StreamReader | None, limit: int) -> tuple[bytes, bool]:
    """Read everything (so the child never blocks on a full pipe), keep ``limit`` bytes."""
    if stream is None:
        return b"", False
    kept = bytearray()
    truncated = False
    while chunk := await stream.read(_CHUNK):
        room = limit - len(kept)
        if room > 0:
            kept += chunk[:room]
        if len(chunk) > room:
            truncated = True
    return bytes(kept), truncated


async def run_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_s: float,
    cancel: CancelToken,
    max_output_bytes: int,
) -> ProcessResult:
    started = time.monotonic()
    group = await spawn_group(argv, cwd=cwd, env=env)
    proc = group.process
    readers = asyncio.gather(
        _drain(proc.stdout, max_output_bytes), _drain(proc.stderr, max_output_bytes)
    )
    waiter = asyncio.ensure_future(proc.wait())
    cancel_waiter = asyncio.ensure_future(cancel.wait())
    timed_out = cancelled = False
    try:
        done, _ = await asyncio.wait(
            {waiter, cancel_waiter}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
        )
        if waiter not in done:
            timed_out = not done
            cancelled = bool(done)
            group.kill_tree()
            await asyncio.wait_for(waiter, timeout=10)
        (out, out_trunc), (err, err_trunc) = await asyncio.wait_for(readers, timeout=10)
    except BaseException:
        group.kill_tree()
        raise
    finally:
        cancel_waiter.cancel()
        group.close()
    return ProcessResult(
        exit_code=proc.returncode,
        stdout=out.decode("utf-8", errors="replace"),
        stderr=err.decode("utf-8", errors="replace"),
        stdout_truncated=out_trunc,
        stderr_truncated=err_trunc,
        duration_s=round(time.monotonic() - started, 3),
        timed_out=timed_out,
        cancelled=cancelled,
    )
