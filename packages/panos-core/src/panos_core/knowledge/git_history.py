"""Git history as knowledge (FR-019, M8-04).

The last commits (message and changed files) are indexed as one synthetic document,
``git:history``, so questions like "when did we change the payment retry?" find the commit.
It is re-indexed only when HEAD moves. Diffs are not indexed (size, and secrets in old
revisions); the file list points to them.
"""

import hashlib
from pathlib import Path
from typing import Final

from panos_core.knowledge.index import Document
from panos_core.repository import gitcli

URI: Final = "git:history"
MAX_COMMITS: Final = 2_000
_FORMAT: Final = "%x1e%H%x1f%aI%x1f%an%x1f%s%x1f%b%x1f"


def render(raw: str) -> str:
    """``git log --name-only`` output (with our separators) → readable text."""
    out: list[str] = []
    for record in raw.split("\x1e"):
        parts = record.split("\x1f")
        if len(parts) < 6:
            continue
        sha, date, author, subject, body, files = parts[:6]
        out.append(f"commit {sha[:12]} {date[:10]} {author}: {subject.strip()}")
        out += [f"    {line}" for line in body.strip().splitlines() if line.strip()]
        changed = [f.strip() for f in files.strip().splitlines() if f.strip()]
        if changed:
            out.append(
                "    files: " + ", ".join(changed[:30]) + (" …" if len(changed) > 30 else "")
            )
    return "\n".join(out)


async def history_document(root: Path, *, limit: int = MAX_COMMITS) -> Document | None:
    """The commit history document, or ``None`` if this is not a Git repository with commits."""
    if not await gitcli.is_repository(root):
        return None
    try:
        raw = await gitcli.run_git(
            root, "log", f"--max-count={limit}", f"--format={_FORMAT}", "--name-only"
        )
    except gitcli.GitError:
        return None  # e.g. a repository without commits yet
    text = render(raw)
    if not text:
        return None
    data = text.encode("utf-8")
    return Document(
        uri=URI,
        text=text,
        content_hash=hashlib.sha256(data).hexdigest(),
        size=len(data),
    )
