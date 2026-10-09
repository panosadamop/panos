"""Create files readable and writable only by the current user (ADR-003)."""

import sys
from pathlib import Path


def create_owner_only_file(path: Path) -> None:
    """Create an empty file at ``path`` that only the current user can access.

    Fails if the file already exists, so callers cannot accidentally reuse a file
    with broader permissions.
    """
    if sys.platform == "win32":
        from panos_core.platform.windows.secure_file import create_owner_only_file as impl
    else:
        from panos_core.platform.posix.secure_file import create_owner_only_file as impl
    impl(path)


def is_owner_only(path: Path) -> bool:
    """Return True if only the current user has access to ``path``."""
    if sys.platform == "win32":
        from panos_core.platform.windows.secure_file import is_owner_only as impl
    else:
        from panos_core.platform.posix.secure_file import is_owner_only as impl
    return impl(path)
