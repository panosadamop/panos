"""Test doubles shared across suites."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from panos_core.apps.catalog import CatalogEntry
from panos_core.platform.desktop import DiscoveredApp, UnsupportedError, WindowInfo

FAKE_INSTALLED = (
    "jetbrains.pycharm",
    "jetbrains.idea",
    "jetbrains.webstorm",
    "vscode",
    "browser.chrome",
)


@dataclass
class FakeDesktop:
    """Records launches instead of starting processes; discovery is deterministic."""

    installed: tuple[str, ...] = FAKE_INSTALLED
    platform: str = "fake"
    launched: list[tuple[str, list[str]]] = field(default_factory=list)
    opened: list[Path] = field(default_factory=list)
    window: WindowInfo | None = None

    def capabilities(self) -> dict[str, str]:
        return {
            "discover_apps": "supported",
            "launch": "supported",
            "open_path": "supported",
            "active_window": "supported" if self.window else "unsupported",
            "focus_window": "unsupported",
        }

    def discover_apps(self, catalog: Sequence[CatalogEntry]) -> list[DiscoveredApp]:
        return [
            DiscoveredApp(e.key, f"C:/Fake/{e.key}/bin/app.exe", "1.0", "fake")
            for e in catalog
            if e.key in self.installed
        ]

    def launch(self, executable: str, args: Sequence[str]) -> int:
        self.launched.append((executable, list(args)))
        return 4242

    def open_path(self, path: Path) -> None:
        self.opened.append(path)

    def active_window(self) -> WindowInfo:
        if self.window is None:
            raise UnsupportedError("no window")
        return self.window

    def focus_window(self, pid: int) -> bool:
        raise UnsupportedError("focus not supported in fake")
