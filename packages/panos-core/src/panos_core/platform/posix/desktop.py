"""macOS and Linux desktop adapters (partial until post-MVP hardening, ADR-022).

Discovery, launch and open_path are implemented. Active-window metadata and focus are
reported as unsupported for now: they need Accessibility/Screen Recording permissions on
macOS and differ between X11 and Wayland on Linux (planned for M7).
"""

import os
import plistlib
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from panos_core.apps.catalog import CatalogEntry
from panos_core.platform.desktop import (
    EXECUTABLE_EXTENSIONS,
    DiscoveredApp,
    UnsupportedError,
    WindowInfo,
)


def _child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("PANOS_")}


class _PosixDesktop:
    platform = "posix"
    _opener = "xdg-open"

    def capabilities(self) -> dict[str, str]:
        return {
            "discover_apps": "supported",
            "launch": "supported",
            "open_path": "supported",
            "active_window": "unsupported",
            "focus_window": "unsupported",
        }

    def open_path(self, path: Path) -> None:
        if path.is_file() and (
            path.suffix.lower() in EXECUTABLE_EXTENSIONS or os.access(path, os.X_OK)
        ):
            raise PermissionError("refusing to open an executable file with its default handler")
        opener = shutil.which(self._opener)
        if opener is None:
            raise UnsupportedError(f"{self._opener} not available")
        subprocess.Popen(  # noqa: S603 - fixed opener, argv list
            [opener, str(path)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_child_env(),
        )

    def active_window(self) -> WindowInfo:
        raise UnsupportedError("active window metadata is not implemented on this platform yet")

    def focus_window(self, pid: int) -> bool:
        raise UnsupportedError("window focus is not implemented on this platform yet")


class MacDesktop(_PosixDesktop):
    platform = "macos"
    _opener = "open"

    def discover_apps(self, catalog: Sequence[CatalogEntry]) -> list[DiscoveredApp]:
        roots = [
            Path("/Applications"),
            Path.home() / "Applications",
            Path.home() / "Applications" / "JetBrains Toolbox",
        ]
        found = []
        for app in catalog:
            for root in roots:
                hit = next((root / b for b in app.mac_bundles if (root / b).is_dir()), None)
                if hit is not None:
                    found.append(
                        DiscoveredApp(app.key, str(hit), _bundle_version(hit), "applications-dir")
                    )
                    break
        return found

    def launch(self, executable: str, args: Sequence[str]) -> int:
        proc = subprocess.Popen(  # noqa: S603 - catalogued bundle via /usr/bin/open
            ["/usr/bin/open", "-n", "-a", executable, "--args", *args],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_child_env(),
        )
        return proc.pid


def _bundle_version(bundle: Path) -> str | None:
    try:
        with (bundle / "Contents" / "Info.plist").open("rb") as handle:
            value = plistlib.load(handle).get("CFBundleShortVersionString")
    except (OSError, plistlib.InvalidFileException):
        return None
    return str(value) if value else None


class LinuxDesktop(_PosixDesktop):
    platform = "linux"

    def discover_apps(self, catalog: Sequence[CatalogEntry]) -> list[DiscoveredApp]:
        extra = [
            str(Path.home() / ".local/share/JetBrains/Toolbox/scripts"),
            "/snap/bin",
            "/var/lib/flatpak/exports/bin",
            str(Path.home() / ".local/bin"),
        ]
        search = os.pathsep.join([os.environ.get("PATH", ""), *extra])
        found = []
        for app in catalog:
            for name in app.linux_bins:
                path = shutil.which(name, path=search)
                if path:
                    found.append(DiscoveredApp(app.key, path, None, "path"))
                    break
        return found

    def launch(self, executable: str, args: Sequence[str]) -> int:
        proc = subprocess.Popen(  # noqa: S603 - catalogued executable, argv list
            [executable, *args],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_child_env(),
        )
        return proc.pid
