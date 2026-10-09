"""Project-scoped path resolution (M2-06).

``resolve_in_root`` is the single gate between a user/model-supplied path and the
filesystem. It rejects:

* empty paths and NUL bytes;
* Windows namespace/device paths (``\\\\?\\``, ``\\\\.\\``), alternate data streams
  (``file.txt:stream``), drive-relative paths (``C:foo``), reserved device names
  (``CON``, ``NUL``, ``COM1``, ...) and components with trailing dots/spaces, which
  Windows silently strips;
* anything that, after resolving ``..``, symlinks and junctions, lies outside the root.

Hard links cannot be detected by path inspection; that residual risk is accepted.
"""

import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final

_RESERVED_WINDOWS: Final = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
    | {"COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}
)
_NAMESPACE_PREFIX_RE: Final = re.compile(r"^[\\/]{2}[?.][\\/]")
_IS_WINDOWS: Final = os.name == "nt"


class PathPolicyError(ValueError):
    """The path is not acceptable inside the project scope."""


def _check_windows_syntax(raw: str) -> None:
    if _NAMESPACE_PREFIX_RE.match(raw):
        raise PathPolicyError("device/namespace paths are not allowed")
    pure = PureWindowsPath(raw)
    if pure.drive and not pure.root and not pure.drive.startswith("\\\\"):
        raise PathPolicyError("drive-relative paths are not allowed")
    rest = raw[len(pure.drive) :] if pure.drive and ":" in pure.drive else raw
    if ":" in rest:
        raise PathPolicyError("alternate data streams are not allowed")
    for part in pure.parts[1 if pure.anchor else 0 :]:
        if part in (".", ".."):
            continue
        if part != part.rstrip(" ."):
            raise PathPolicyError(f"path component with trailing dot/space: {part!r}")
        stem = part.split(".")[0].upper()
        if stem in _RESERVED_WINDOWS:
            raise PathPolicyError(f"reserved device name: {part!r}")


def resolve_in_root(root: Path, raw: str, *, windows: bool = _IS_WINDOWS) -> Path:
    """Resolve ``raw`` (relative to ``root`` or absolute) and ensure it stays inside ``root``."""
    if not raw or not raw.strip():
        raise PathPolicyError("empty path")
    if "\x00" in raw:
        raise PathPolicyError("NUL byte in path")
    if windows:
        _check_windows_syntax(raw)

    root_resolved = root.resolve(strict=True)
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    resolved = candidate.resolve(strict=False)  # follows symlinks/junctions that exist
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise PathPolicyError("path escapes the project directory")
    return resolved


def relative_to_root(root: Path, path: Path) -> str:
    """POSIX-style relative path for payloads/audit (stable across platforms)."""
    rel = path.relative_to(root.resolve())
    return PurePosixPath(*rel.parts).as_posix() if rel.parts else "."


def is_git_internal(root: Path, path: Path) -> bool:
    rel = path.relative_to(root.resolve())
    return bool(rel.parts) and rel.parts[0].lower() == ".git"
