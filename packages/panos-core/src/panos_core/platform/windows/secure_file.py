"""Windows owner-only files via a protected DACL granting access to the current user only."""

# PySID exists only in the type stubs, so annotations must not be evaluated at runtime.
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

assert sys.platform == "win32"  # noqa: S101 - imported only on Windows by the dispatcher

import ntsecuritycon  # noqa: E402 - after the platform assert (mypy)
import win32api  # noqa: E402 - after the platform assert (mypy)
import win32security  # noqa: E402 - after the platform assert (mypy)

if TYPE_CHECKING:
    from _win32typing import PyACL, PySID

_DACL_FLAGS = (
    win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION
)


def _current_user_sid() -> PySID:
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        sid: PySID = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        return sid
    finally:
        win32api.CloseHandle(token)


def _apply_owner_only_dacl(path: Path) -> None:
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(
        win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, _current_user_sid()
    )
    win32security.SetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT, _DACL_FLAGS, None, None, dacl, None
    )


def create_owner_only_file(path: Path) -> None:
    # Exclusive create; the file is empty until the DACL is restricted.
    with path.open("xb"):
        pass
    try:
        _apply_owner_only_dacl(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def is_owner_only(path: Path) -> bool:
    sd = win32security.GetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
    )
    # The stubs say non-None, but a NULL DACL is possible and grants everyone access.
    dacl: PyACL | None = sd.GetSecurityDescriptorDacl()
    if dacl is None:
        return False
    user = _current_user_sid()
    for index in range(dacl.GetAceCount()):
        (_ace_type, _flags), _mask, sid = dacl.GetAce(index)
        if sid != user:
            return False
    return dacl.GetAceCount() > 0
