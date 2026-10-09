"""Windows process groups backed by a Job Object (KILL_ON_JOB_CLOSE)."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

assert sys.platform == "win32"  # noqa: S101 - imported only on Windows by the dispatcher

import psutil  # noqa: E402
import win32api  # noqa: E402 - after the platform assert (mypy)
import win32con  # noqa: E402
import win32job  # noqa: E402


class WindowsProcessGroup:
    def __init__(self, process: asyncio.subprocess.Process, job: Any) -> None:
        self.process = process
        self._job = job

    def kill_tree(self) -> None:
        if self._job is not None:
            try:
                win32job.TerminateJobObject(self._job, 1)
                return
            except win32api.error:
                pass
        # Fallback: walk the tree (processes started before job assignment, or no job).
        try:
            parent = psutil.Process(self.process.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.Error:
            pass

    def close(self) -> None:
        if self._job is not None:
            win32api.CloseHandle(self._job)
            self._job = None


def _create_job() -> Any:
    # types-pywin32 annotates CreateJobObject as returning None (stub bug): use Any.
    try:
        job: Any = win32job.CreateJobObject(None, "")  # type: ignore[func-returns-value]
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        return job
    except win32api.error:
        return None


async def spawn(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str]) -> WindowsProcessGroup:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=dict(env),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
    )
    job = _create_job()
    if job is not None:
        try:
            handle = win32api.OpenProcess(
                win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid
            )
            try:
                win32job.AssignProcessToJobObject(job, handle)
            finally:
                win32api.CloseHandle(handle)
        except win32api.error:
            win32api.CloseHandle(job)
            job = None
    return WindowsProcessGroup(process, job)
