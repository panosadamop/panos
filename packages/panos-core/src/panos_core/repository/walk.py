"""Project file walking that honours ``.gitignore`` and skips heavy/vendored directories."""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pathspec

SKIP_DIRS: Final = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        ".nox",
        "target",
        "dist",
        "build",
        "out",
        ".idea",
        ".vscode",
        ".gradle",
        ".next",
        ".nuxt",
        "coverage",
        "htmlcov",
        "vendor",
        "bin",
        "obj",
        ".terraform",
        ".cache",
        ".panos",
    }
)


@dataclass(frozen=True)
class WalkedFile:
    path: Path  # absolute
    rel: str  # POSIX-style relative path
    size: int


def load_gitignore(root: Path) -> pathspec.GitIgnoreSpec | None:
    ignore = root / ".gitignore"
    if not ignore.is_file():
        return None
    try:
        lines = ignore.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return pathspec.GitIgnoreSpec.from_lines(lines)


def iter_project_files(root: Path, *, max_files: int = 50_000) -> Iterator[WalkedFile]:
    """Yield regular files (no symlinks), skipping ignored and vendored paths.

    Only the root ``.gitignore`` is honoured (nested ignore files: known limitation).
    """
    spec = load_gitignore(root)
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        rel_dir = current.relative_to(root).as_posix()
        kept = []
        for d in sorted(dirnames):
            if d in SKIP_DIRS or (current / d).is_symlink():
                continue
            rel = f"{rel_dir}/{d}/" if rel_dir != "." else f"{d}/"
            if spec is not None and spec.match_file(rel):
                continue
            kept.append(d)
        dirnames[:] = kept
        for name in sorted(filenames):
            full = current / name
            rel = f"{rel_dir}/{name}" if rel_dir != "." else name
            if spec is not None and spec.match_file(rel):
                continue
            try:
                if full.is_symlink() or not full.is_file():
                    continue
                size = full.stat().st_size
            except OSError:
                continue
            yield WalkedFile(full, rel, size)
            count += 1
            if count >= max_files:
                return
