"""Read-only Git facts used for policy classification (not exposed as a tool)."""

import asyncio
import shutil
import subprocess
from pathlib import Path

_TIMEOUT_S = 10


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        return subprocess.run(  # noqa: S603 - fixed executable, argv list, no shell
            [git, "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _tracked_and_clean(root: Path, rel: str) -> bool:
    tracked = _git(root, "ls-files", "--error-unmatch", "--", rel)
    if tracked is None or tracked.returncode != 0:
        return False
    status = _git(root, "status", "--porcelain", "--", rel)
    return status is not None and status.returncode == 0 and status.stdout.strip() == ""


async def is_tracked_and_clean(root: Path, rel: str) -> bool:
    """True if ``rel`` is tracked by Git with no uncommitted changes (recoverable)."""
    return await asyncio.to_thread(_tracked_and_clean, root, rel)
