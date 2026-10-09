"""Desktop adapter contract (ARCHITECTURE.md "OS adapter interface", ADR-022).

Every OS provides the same operations. A capability an OS cannot provide raises
:class:`UnsupportedError`, which callers report instead of crashing (contract tests
enforce this on each platform).
"""

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from panos_core.apps.catalog import CatalogEntry

# Opening these with the OS default handler would execute them (os.open_path refuses).
EXECUTABLE_EXTENSIONS: Final = frozenset(
    {
        ".exe",
        ".bat",
        ".cmd",
        ".com",
        ".scr",
        ".pif",
        ".msi",
        ".msp",
        ".ps1",
        ".psm1",
        ".vbs",
        ".vbe",
        ".js",
        ".jse",
        ".wsf",
        ".wsh",
        ".hta",
        ".lnk",
        ".url",
        ".reg",
        ".cpl",
        ".jar",
        ".app",
        ".command",
        ".sh",
        ".desktop",
        ".appimage",
        ".run",
        ".py",
        ".pyw",
        ".pl",
        ".rb",
    }
)


class UnsupportedError(RuntimeError):
    """The capability is not available on this platform/session (e.g. Wayland)."""


@dataclass(frozen=True)
class DiscoveredApp:
    key: str
    executable: str
    version: str | None
    source: str


@dataclass(frozen=True)
class WindowInfo:
    title: str
    pid: int
    process_name: str | None
    executable: str | None
    bounds: tuple[int, int, int, int] | None  # left, top, right, bottom


class DesktopAdapter(Protocol):
    platform: str

    def capabilities(self) -> dict[str, str]: ...
    def discover_apps(self, catalog: Sequence[CatalogEntry]) -> list[DiscoveredApp]: ...
    def launch(self, executable: str, args: Sequence[str]) -> int: ...
    def open_path(self, path: Path) -> None: ...
    def active_window(self) -> WindowInfo: ...
    def focus_window(self, pid: int) -> bool: ...


def default_desktop() -> DesktopAdapter:
    # if/else on sys.platform lets mypy prune the branch for other OSes without warnings.
    if sys.platform == "win32":
        from panos_core.platform.windows.desktop import WindowsDesktop

        return WindowsDesktop()
    else:
        from panos_core.platform.posix.desktop import LinuxDesktop, MacDesktop

        return MacDesktop() if sys.platform == "darwin" else LinuxDesktop()
