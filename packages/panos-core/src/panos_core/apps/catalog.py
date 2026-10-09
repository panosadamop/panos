"""Catalog of supported applications (BR-006: JetBrains IDEs, VS Code, Sublime, browsers).

Only catalogued applications can be discovered and launched: the catalog is the
allowlist behind "launch approved application" (PERMISSIONS.md, P1).
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class CatalogEntry:
    key: str
    name: str
    kind: str  # "ide" | "editor" | "browser"
    aliases: tuple[str, ...]
    technologies: tuple[str, ...] = ()  # used to pick a default IDE per stack
    windows_exes: tuple[str, ...] = ()
    windows_display: str = ""  # case-insensitive prefix of the uninstall DisplayName
    windows_dirs: tuple[str, ...] = ()  # %LOCALAPPDATA%\Programs / Program Files dir names
    mac_bundles: tuple[str, ...] = ()
    linux_bins: tuple[str, ...] = ()


def _jb(
    key: str,
    name: str,
    exe: str,
    dirs: tuple[str, ...],
    bundles: tuple[str, ...],
    bins: tuple[str, ...],
    aliases: tuple[str, ...],
    tech: tuple[str, ...],
) -> CatalogEntry:
    return CatalogEntry(
        key=f"jetbrains.{key}",
        name=name,
        kind="ide",
        aliases=aliases,
        technologies=tech,
        windows_exes=(exe,),
        windows_display=name,
        windows_dirs=dirs,
        mac_bundles=bundles,
        linux_bins=bins,
    )


CATALOG: Final[tuple[CatalogEntry, ...]] = (
    _jb(
        "idea",
        "IntelliJ IDEA",
        "idea64.exe",
        ("IntelliJ IDEA Ultimate", "IntelliJ IDEA Community Edition", "IntelliJ IDEA"),
        ("IntelliJ IDEA.app", "IntelliJ IDEA Ultimate.app", "IntelliJ IDEA CE.app"),
        ("idea", "intellij-idea-ultimate", "intellij-idea-community"),
        ("intellij", "idea", "intellij idea"),
        ("Java", "Kotlin", "Spring Boot", "Vaadin", "Groovy", "Scala", "maven", "gradle"),
    ),
    _jb(
        "pycharm",
        "PyCharm",
        "pycharm64.exe",
        ("PyCharm Professional", "PyCharm Community Edition", "PyCharm"),
        ("PyCharm.app", "PyCharm Professional Edition.app", "PyCharm CE.app"),
        ("pycharm", "pycharm-professional", "pycharm-community"),
        ("pycharm",),
        ("Python", "Django", "FastAPI", "Flask"),
    ),
    _jb(
        "webstorm",
        "WebStorm",
        "webstorm64.exe",
        ("WebStorm",),
        ("WebStorm.app",),
        ("webstorm",),
        ("webstorm",),
        ("TypeScript", "JavaScript", "React", "NestJS", "Vue", "Angular", "node"),
    ),
    _jb(
        "phpstorm",
        "PhpStorm",
        "phpstorm64.exe",
        ("PhpStorm",),
        ("PhpStorm.app",),
        ("phpstorm",),
        ("phpstorm",),
        ("PHP", "Laravel", "Symfony"),
    ),
    _jb(
        "rider",
        "Rider",
        "rider64.exe",
        ("Rider", "JetBrains Rider"),
        ("Rider.app",),
        ("rider",),
        ("rider",),
        ("C#", "F#", "dotnet"),
    ),
    _jb(
        "goland",
        "GoLand",
        "goland64.exe",
        ("GoLand",),
        ("GoLand.app",),
        ("goland",),
        ("goland",),
        ("Go", "go"),
    ),
    _jb(
        "clion",
        "CLion",
        "clion64.exe",
        ("CLion",),
        ("CLion.app",),
        ("clion",),
        ("clion",),
        ("C", "C++"),
    ),
    _jb(
        "rustrover",
        "RustRover",
        "rustrover64.exe",
        ("RustRover",),
        ("RustRover.app",),
        ("rustrover",),
        ("rustrover",),
        ("Rust", "cargo"),
    ),
    _jb(
        "datagrip",
        "DataGrip",
        "datagrip64.exe",
        ("DataGrip",),
        ("DataGrip.app",),
        ("datagrip",),
        ("datagrip",),
        ("SQL",),
    ),
    CatalogEntry(
        key="android.studio",
        name="Android Studio",
        kind="ide",
        aliases=("android studio", "studio"),
        technologies=("Dart",),
        windows_exes=("studio64.exe",),
        windows_display="Android Studio",
        windows_dirs=("Android Studio", "Android\\Android Studio"),
        mac_bundles=("Android Studio.app",),
        linux_bins=("android-studio", "studio"),
    ),
    CatalogEntry(
        key="vscode",
        name="Visual Studio Code",
        kind="editor",
        aliases=("vscode", "vs code", "code", "visual studio code"),
        windows_exes=("Code.exe",),
        windows_display="Microsoft Visual Studio Code",
        windows_dirs=("Microsoft VS Code",),
        mac_bundles=("Visual Studio Code.app",),
        linux_bins=("code",),
    ),
    CatalogEntry(
        key="sublime",
        name="Sublime Text",
        kind="editor",
        aliases=("sublime", "sublime text", "subl"),
        windows_exes=("sublime_text.exe",),
        windows_display="Sublime Text",
        windows_dirs=("Sublime Text", "Sublime Text 3"),
        mac_bundles=("Sublime Text.app",),
        linux_bins=("subl", "sublime_text"),
    ),
    CatalogEntry(
        key="browser.chrome",
        name="Google Chrome",
        kind="browser",
        aliases=("chrome", "google chrome"),
        windows_exes=("chrome.exe",),
        windows_display="Google Chrome",
        windows_dirs=("Google\\Chrome\\Application",),
        mac_bundles=("Google Chrome.app",),
        linux_bins=("google-chrome", "google-chrome-stable", "chromium"),
    ),
    CatalogEntry(
        key="browser.edge",
        name="Microsoft Edge",
        kind="browser",
        aliases=("edge", "microsoft edge"),
        windows_exes=("msedge.exe",),
        windows_display="Microsoft Edge",
        windows_dirs=("Microsoft\\Edge\\Application",),
        mac_bundles=("Microsoft Edge.app",),
        linux_bins=("microsoft-edge",),
    ),
    CatalogEntry(
        key="browser.firefox",
        name="Mozilla Firefox",
        kind="browser",
        aliases=("firefox", "mozilla firefox"),
        windows_exes=("firefox.exe",),
        windows_display="Mozilla Firefox",
        windows_dirs=("Mozilla Firefox",),
        mac_bundles=("Firefox.app",),
        linux_bins=("firefox",),
    ),
    CatalogEntry(
        key="browser.brave",
        name="Brave",
        kind="browser",
        aliases=("brave", "brave browser"),
        windows_exes=("brave.exe",),
        windows_display="Brave",
        windows_dirs=("BraveSoftware\\Brave-Browser\\Application",),
        mac_bundles=("Brave Browser.app",),
        linux_bins=("brave-browser", "brave"),
    ),
)

_BY_KEY: Final = {e.key: e for e in CATALOG}


def find_entry(ref: str) -> CatalogEntry | None:
    """Resolve a key or alias (case-insensitive)."""
    low = ref.strip().lower()
    if low in _BY_KEY:
        return _BY_KEY[low]
    for entry in CATALOG:
        if low == entry.name.lower() or low in entry.aliases:
            return entry
    return None
