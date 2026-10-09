import pytest

from panos_core.apps.catalog import CATALOG, find_entry

pytestmark = pytest.mark.req("FR-003", "BR-006")


@pytest.mark.parametrize(
    ("ref", "key"),
    [
        ("pycharm", "jetbrains.pycharm"),
        ("PyCharm", "jetbrains.pycharm"),
        ("intellij", "jetbrains.idea"),
        ("IntelliJ IDEA", "jetbrains.idea"),
        ("vs code", "vscode"),
        ("code", "vscode"),
        ("subl", "sublime"),
        ("chrome", "browser.chrome"),
        ("Edge", "browser.edge"),
        ("firefox", "browser.firefox"),
        ("jetbrains.webstorm", "jetbrains.webstorm"),
    ],
)
def test_aliases_resolve(ref: str, key: str) -> None:
    entry = find_entry(ref)
    assert entry is not None and entry.key == key


def test_unknown_apps_are_not_resolvable() -> None:
    assert find_entry("notepad") is None
    assert find_entry("cmd") is None
    assert find_entry("powershell") is None


def test_catalog_covers_required_families() -> None:
    kinds = {e.kind for e in CATALOG}
    assert kinds == {"ide", "editor", "browser"}
    keys = {e.key for e in CATALOG}
    assert {"jetbrains.idea", "jetbrains.pycharm", "vscode", "sublime"} <= keys
    assert len(keys) == len(CATALOG)


def test_aliases_are_unique_across_entries() -> None:
    seen: dict[str, str] = {}
    for entry in CATALOG:
        for alias in entry.aliases:
            assert alias not in seen, f"{alias} used by {seen.get(alias)} and {entry.key}"
            seen[alias] = entry.key
