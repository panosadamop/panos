"""Windows desktop adapter (first production OS adapter, ADR-022).

Discovery sources, in priority order:
1. uninstall registry entries (HKLM, HKLM WOW6432Node, HKCU);
2. App Paths registry keys;
3. well-known install directories (%LOCALAPPDATA%\\Programs, Program Files) — this is
   where JetBrains Toolbox installs IDEs.

Applications are launched detached with CreateProcess (argv list, never a shell), so
they outlive Panos and are not part of any tool job object.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

assert sys.platform == "win32"  # noqa: S101 - imported only on Windows by the dispatcher

import winreg  # noqa: E402

import psutil  # noqa: E402
import win32con  # noqa: E402 - after the platform assert (mypy)
import win32gui  # noqa: E402
import win32process  # noqa: E402

from panos_core.apps.catalog import CatalogEntry  # noqa: E402
from panos_core.platform.desktop import (  # noqa: E402
    EXECUTABLE_EXTENSIONS,
    DiscoveredApp,
    UnsupportedError,
    WindowInfo,
)

_UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
_APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
_HIVES = (
    (winreg.HKEY_LOCAL_MACHINE, _UNINSTALL, winreg.KEY_WOW64_64KEY),
    (winreg.HKEY_LOCAL_MACHINE, _UNINSTALL, winreg.KEY_WOW64_32KEY),
    (winreg.HKEY_CURRENT_USER, _UNINSTALL, 0),
)
_DETACHED = 0x00000008  # DETACHED_PROCESS


def _values(key: winreg.HKEYType) -> dict[str, str]:
    out: dict[str, str] = {}
    i = 0
    while True:
        try:
            name, value, _ = winreg.EnumValue(key, i)
        except OSError:
            return out
        if isinstance(value, str):
            out[name] = value
        i += 1


def _subkeys(hive: int, path: str, flags: int) -> Iterator[dict[str, str]]:
    try:
        root = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | flags)
    except OSError:
        return
    with root:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                return
            i += 1
            try:
                with winreg.OpenKey(root, name) as sub:
                    values = _values(sub)
                    values["__key__"] = name
                    yield values
            except OSError:
                continue


def _icon_path(icon: str) -> Path | None:
    if not icon:
        return None
    path = icon.strip().strip('"')
    path = re.sub(r",\s*-?\d+$", "", path).strip('"')
    return Path(path) if path else None


def _find_exe(base: Path, exes: Sequence[str]) -> Path | None:
    for exe in exes:
        for candidate in (base / exe, base / "bin" / exe, base / "Application" / exe):
            if candidate.is_file():
                return candidate
    return None


class WindowsDesktop:
    platform = "windows"

    def capabilities(self) -> dict[str, str]:
        return {
            "discover_apps": "supported",
            "launch": "supported",
            "open_path": "supported",
            "active_window": "supported",
            "focus_window": "supported",
        }

    # ------------------------------------------------------------------ discovery
    def discover_apps(self, catalog: Sequence[CatalogEntry]) -> list[DiscoveredApp]:
        found: dict[str, DiscoveredApp] = {}
        entries = [v for hive, path, flags in _HIVES for v in _subkeys(hive, path, flags)]
        for app in catalog:
            if not app.windows_exes:
                continue
            if app.kind == "browser":
                # Browser uninstall entries often point into version folders that move on
                # every update; App Paths holds the stable launcher.
                hit = self._from_app_paths(app) or self._from_uninstall(app, entries)
            else:
                hit = self._from_uninstall(app, entries) or self._from_app_paths(app)
            hit = hit or self._from_dirs(app)
            if hit is not None:
                found[app.key] = hit
        return list(found.values())

    @staticmethod
    def _from_uninstall(app: CatalogEntry, entries: list[dict[str, str]]) -> DiscoveredApp | None:
        prefix = app.windows_display.lower()
        for e in entries:
            name = e.get("DisplayName", "")
            if not prefix or not name.lower().startswith(prefix):
                continue
            if "webview" in name.lower():  # "Microsoft Edge WebView2 Runtime" is not Edge
                continue
            exe = _icon_path(e.get("DisplayIcon", ""))
            if (
                exe is None
                or exe.name.lower() not in {x.lower() for x in app.windows_exes}
                or not exe.is_file()
            ):
                location = e.get("InstallLocation", "").strip().strip('"')
                exe = _find_exe(Path(location), app.windows_exes) if location else None
            if exe is not None and exe.is_file():
                return DiscoveredApp(
                    app.key, str(exe), e.get("DisplayVersion") or None, "registry-uninstall"
                )
        return None

    @staticmethod
    def _from_app_paths(app: CatalogEntry) -> DiscoveredApp | None:
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for exe in app.windows_exes:
                try:
                    with winreg.OpenKey(hive, rf"{_APP_PATHS}\{exe}") as key:
                        value, _ = winreg.QueryValueEx(key, "")
                except OSError:
                    continue
                path = Path(str(value).strip('"'))
                if path.is_file():
                    return DiscoveredApp(app.key, str(path), None, "registry-app-paths")
        return None

    @staticmethod
    def _from_dirs(app: CatalogEntry) -> DiscoveredApp | None:
        bases = [
            os.environ.get("LOCALAPPDATA", "") + r"\Programs",
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        ]
        for base in filter(None, bases):
            for directory in app.windows_dirs:
                exe = _find_exe(Path(base) / directory, app.windows_exes)
                if exe is not None:
                    return DiscoveredApp(app.key, str(exe), None, "install-dir")
        return None

    # ------------------------------------------------------------------ launch / open
    def launch(self, executable: str, args: Sequence[str]) -> int:
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PANOS_")}
        proc = subprocess.Popen(  # noqa: S603 - catalogued executable, argv list, no shell
            [executable, *args],
            creationflags=_DETACHED | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env=env,
        )
        return proc.pid

    def open_path(self, path: Path) -> None:
        if path.is_file() and path.suffix.lower() in EXECUTABLE_EXTENSIONS:
            raise PermissionError("refusing to open an executable file with its default handler")
        os.startfile(str(path))  # noqa: S606 - directory or non-executable file only

    # ------------------------------------------------------------------ windows
    def active_window(self) -> WindowInfo:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            raise UnsupportedError("no foreground window")
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        name = exe = None
        try:
            proc = psutil.Process(pid)
            name, exe = proc.name(), proc.exe()
        except psutil.Error:
            pass
        try:
            bounds = tuple(win32gui.GetWindowRect(hwnd))
        except win32gui.error:
            bounds = None
        return WindowInfo(
            win32gui.GetWindowText(hwnd),
            pid,
            name,
            exe,
            bounds if bounds and len(bounds) == 4 else None,
        )

    def focus_window(self, pid: int) -> bool:
        handles: list[int] = []

        def collect(hwnd: int, _: object) -> bool:
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
                _, owner = win32process.GetWindowThreadProcessId(hwnd)
                if owner == pid:
                    handles.append(hwnd)
            return True

        win32gui.EnumWindows(collect, None)
        if not handles:
            return False
        hwnd = handles[0]
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except win32gui.error:
            return False  # Windows foreground-lock rules may refuse; reported, not forced
        return True
