"""Async Git CLI helpers with porcelain parsing (read-only operations).

Git runs with a scrubbed environment, no pager, no interactive prompts and a timeout.
Remote URLs are redacted because they can embed credentials.
"""

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from panos_core.redaction import redact_text
from panos_core.shell.environment import build_child_env
from panos_core.shell.process import run_process
from panos_core.tools.contract import CancelToken

_TIMEOUT_S = 60
_MAX_OUTPUT = 5_000_000


class GitError(RuntimeError):
    pass


def git_available() -> bool:
    return shutil.which("git") is not None


async def run_git(
    root: Path, *args: str, timeout_s: float = _TIMEOUT_S, cancel: CancelToken | None = None
) -> str:
    git = shutil.which("git")
    if git is None:
        raise GitError("git is not installed")
    env, _ = build_child_env(os.environ, {})
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        }
    )
    result = await run_process(
        [git, "--no-pager", *args],
        cwd=root,
        env=env,
        timeout_s=timeout_s,
        cancel=cancel or CancelToken(),
        max_output_bytes=_MAX_OUTPUT,
    )
    if result.timed_out:
        raise GitError(f"git {args[0]} timed out")
    if result.exit_code != 0:
        raise GitError(f"git {args[0]} failed: {result.stderr.strip()[:500]}")
    return result.stdout


async def is_repository(root: Path) -> bool:
    if not git_available():
        return False
    try:
        out = await run_git(root, "rev-parse", "--is-inside-work-tree")
    except GitError:
        return False
    return out.strip() == "true"


@dataclass
class StatusEntry:
    path: str
    index: str  # X in porcelain v1/v2 terms
    worktree: str  # Y
    kind: str  # "changed", "renamed", "unmerged", "untracked", "ignored"
    orig_path: str | None = None


@dataclass
class GitStatus:
    branch: str | None = None
    head: str | None = None
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    entries: list[StatusEntry] = field(default_factory=list)

    @property
    def dirty(self) -> bool:
        return any(e.kind != "untracked" for e in self.entries)


def parse_status_v2(text: str) -> GitStatus:
    """Parse ``git status --porcelain=v2 --branch -z``."""
    status = GitStatus()
    records = text.split("\x00")
    i = 0
    while i < len(records):
        rec = records[i]
        i += 1
        if not rec:
            continue
        if rec.startswith("# branch.oid "):
            oid = rec.split(" ", 2)[2]
            status.head = None if oid == "(initial)" else oid
        elif rec.startswith("# branch.head "):
            head = rec.split(" ", 2)[2]
            status.branch = None if head == "(detached)" else head
        elif rec.startswith("# branch.upstream "):
            status.upstream = rec.split(" ", 2)[2]
        elif rec.startswith("# branch.ab "):
            m = re.match(r"# branch\.ab \+(\d+) -(\d+)", rec)
            if m:
                status.ahead, status.behind = int(m[1]), int(m[2])
        elif rec.startswith("1 "):
            parts = rec.split(" ", 8)
            status.entries.append(StatusEntry(parts[8], parts[1][0], parts[1][1], "changed"))
        elif rec.startswith("2 "):
            parts = rec.split(" ", 9)
            orig = records[i] if i < len(records) else None
            i += 1
            status.entries.append(
                StatusEntry(parts[9], parts[1][0], parts[1][1], "renamed", orig_path=orig)
            )
        elif rec.startswith("u "):
            parts = rec.split(" ", 10)
            status.entries.append(StatusEntry(parts[10], parts[1][0], parts[1][1], "unmerged"))
        elif rec.startswith("? "):
            status.entries.append(StatusEntry(rec[2:], "?", "?", "untracked"))
        elif rec.startswith("! "):
            status.entries.append(StatusEntry(rec[2:], "!", "!", "ignored"))
    return status


async def status(root: Path) -> GitStatus:
    return parse_status_v2(await run_git(root, "status", "--porcelain=v2", "--branch", "-z"))


@dataclass
class Commit:
    hash: str
    short: str
    author: str
    date: str
    subject: str


_LOG_FORMAT = "%H%x1f%h%x1f%an%x1f%aI%x1f%s%x1e"


def parse_log(text: str) -> list[Commit]:
    commits = []
    for record in text.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x1f")
        if len(parts) == 5:
            commits.append(Commit(*parts))
    return commits


async def log(root: Path, *, limit: int = 20, path: str | None = None) -> list[Commit]:
    args = ["log", f"--max-count={limit}", f"--format={_LOG_FORMAT}"]
    if path:
        args += ["--", path]
    try:
        return parse_log(await run_git(root, *args))
    except GitError as exc:
        if "does not have any commits" in str(exc):
            return []
        raise


async def branches(root: Path) -> list[dict[str, str | bool]]:
    out = await run_git(
        root, "branch", "--all", "--format=%(HEAD)%1f%(refname:short)%1f%(objectname:short)"
    )
    result: list[dict[str, str | bool]] = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            result.append({"current": parts[0] == "*", "name": parts[1], "commit": parts[2]})
    return result


async def remotes(root: Path) -> list[dict[str, str]]:
    out = await run_git(root, "remote", "-v")
    seen: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            seen.setdefault(parts[0], redact_text(parts[1]))
    return [{"name": k, "url": v} for k, v in seen.items()]
