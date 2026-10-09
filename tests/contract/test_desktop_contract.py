"""OS adapter contract (NFR-PORT-002, ADR-022). Runs against the *real* adapter of the
current OS in the CI matrix. It never launches applications or opens files."""

import pytest

from panos_core.apps.catalog import CATALOG, find_entry
from panos_core.platform.desktop import (
    DesktopAdapter,
    DiscoveredApp,
    UnsupportedError,
    WindowInfo,
    default_desktop,
)

pytestmark = pytest.mark.req("NFR-PORT-002", "BR-002")

REQUIRED = {"discover_apps", "launch", "open_path", "active_window", "focus_window"}


@pytest.fixture(scope="module")
def desktop() -> DesktopAdapter:
    return default_desktop()


def test_capabilities_declare_every_operation(desktop: DesktopAdapter) -> None:
    caps = desktop.capabilities()
    assert set(caps) == REQUIRED
    assert set(caps.values()) <= {"supported", "partial", "unsupported"}
    assert caps["discover_apps"] == caps["launch"] == caps["open_path"] == "supported"


def test_discovery_returns_catalogued_existing_apps(desktop: DesktopAdapter) -> None:
    found = desktop.discover_apps(CATALOG)
    assert all(isinstance(a, DiscoveredApp) for a in found)
    assert len({a.key for a in found}) == len(found)
    for app in found:
        assert find_entry(app.key) is not None
        assert app.executable


def test_unsupported_capabilities_raise_typed_error(desktop: DesktopAdapter) -> None:
    caps = desktop.capabilities()
    if caps["active_window"] == "unsupported":
        with pytest.raises(UnsupportedError):
            desktop.active_window()
    else:
        try:
            info = desktop.active_window()
        except UnsupportedError:
            return  # e.g. no interactive session on a CI runner
        assert isinstance(info, WindowInfo)
        assert info.pid >= 0
    if caps["focus_window"] == "unsupported":
        with pytest.raises(UnsupportedError):
            desktop.focus_window(1)


@pytest.mark.windows
@pytest.mark.req("BR-006")
def test_windows_discovers_edge(desktop: DesktopAdapter) -> None:
    """Every supported Windows installation ships Microsoft Edge."""
    keys = {a.key for a in desktop.discover_apps(CATALOG)}
    assert "browser.edge" in keys
