"""POSIX owner-only files via mode 0600."""

import os
import stat
import sys
from pathlib import Path

assert sys.platform != "win32"  # noqa: S101 - imported only on POSIX by the dispatcher

_OWNER_RW = stat.S_IRUSR | stat.S_IWUSR


def create_owner_only_file(path: Path) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _OWNER_RW)
    os.close(fd)
    os.chmod(path, _OWNER_RW)  # in case the umask stripped bits we asked for


def is_owner_only(path: Path) -> bool:
    st = path.stat()
    return st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) & 0o077 == 0
