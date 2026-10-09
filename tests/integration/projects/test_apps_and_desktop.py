"""Application registry and desktop tools (FR-003, BR-006, US-010, AC-US010-01..04)."""

from pathlib import Path

import pytest

from panos_core.apps.service import AppError
from panos_core.domain import Project
from panos_core.platform.desktop import WindowInfo
from panos_core.tools import ToolStatus
from tests.integration.projects.conftest import M3
from tests.repos import copy_repo

pytestmark = pytest.mark.req("FR-003", "US-010")


@pytest.fixture
async def project(m3: M3, tmp_path: Path) -> Project:
    return await m3.projects.register(copy_repo("react_vite_pnpm", tmp_path))


@pytest.mark.req("AC-US010-01", "BR-006")
async def test_discovered_apps_are_listed_and_unavailable_ones_dropped(m3: M3) -> None:
    keys = {a.key for a in await m3.apps.list()}
    assert keys == {
        "jetbrains.pycharm",
        "jetbrains.idea",
        "jetbrains.webstorm",
        "vscode",
        "browser.chrome",
    }
    m3.desktop.installed = ("vscode",)
    assert [a.key for a in await m3.apps.refresh()] == ["vscode"]


@pytest.mark.req("AC-US010-02", "AC-US010-03")
async def test_resolve_by_alias_and_clear_errors(m3: M3) -> None:
    assert (await m3.apps.resolve("intellij")).key == "jetbrains.idea"
    with pytest.raises(AppError) as err:
        await m3.apps.resolve("sublime")
    assert err.value.reason == "not_installed"
    with pytest.raises(AppError) as err:
        await m3.apps.resolve("notepad")
    assert err.value.reason == "unsupported"


async def test_preferred_ide_resolution_order(m3: M3, project: Project) -> None:
    snapshot = await m3.analysis.analyze(project)
    # 1. catalog default for the detected stack (React/TypeScript → WebStorm)
    assert (await m3.apps.preferred_for(project, snapshot)).key == "jetbrains.webstorm"
    # 2. a per-technology user preference wins over the catalog default
    await m3.apps.set_technology_preference("React", "vscode")
    assert (await m3.apps.preferred_for(project, snapshot)).key == "vscode"
    # 3. the project's own preference wins over everything
    project = await m3.projects.set_preferred_ide(project.id, "jetbrains.idea", actor="user")
    assert (await m3.apps.preferred_for(project, snapshot)).key == "jetbrains.idea"


@pytest.mark.req("AC-US010-02", "AC-US010-04")
async def test_project_open_launches_preferred_ide_with_project_path(
    m3: M3, project: Project
) -> None:
    await m3.analysis.analyze(project)
    result = await m3.runtime.invoke("project.open", {}, await m3.ctx(project))
    assert result.status == ToolStatus.SUCCEEDED, result.error
    assert result.action == "app.launch"
    assert m3.desktop.launched == [
        ("C:/Fake/jetbrains.webstorm/bin/app.exe", [str(Path(project.path).resolve())])
    ]
    events = await m3.audit.list(event_type="tool.executed")
    assert events[0].metadata["tool"] == "project.open"


async def test_app_launch_argument_restrictions(m3: M3, project: Project) -> None:
    ctx = await m3.ctx(project)
    ok = await m3.runtime.invoke(
        "app.launch", {"app": "chrome", "target": "url", "url": "https://example.invalid/docs"}, ctx
    )
    assert ok.status == ToolStatus.SUCCEEDED
    for args in (
        {"app": "chrome", "target": "url", "url": "file:///C:/Windows/win.ini"},
        {"app": "chrome", "target": "url", "url": "javascript:alert(1)"},
        {"app": "vscode", "target": "url", "url": "https://example.invalid"},  # not a browser
        {"app": "vscode", "target": "path", "path": "../outside"},
        {"app": "notepad"},
        {"app": "vscode", "extra_args": ["--disable-extensions"]},
    ):
        result = await m3.runtime.invoke("app.launch", args, ctx)
        assert result.status in (ToolStatus.DENIED, ToolStatus.INVALID_INPUT), args
    assert len(m3.desktop.launched) == 1


async def test_open_path_refuses_executables(m3: M3, project: Project) -> None:
    root = Path(project.path)
    (root / "run.bat").write_text("@echo off\n", encoding="utf-8")
    ctx = await m3.ctx(project)
    refused = await m3.runtime.invoke("os.open_path", {"path": "run.bat"}, ctx)
    assert refused.status == ToolStatus.DENIED
    ok = await m3.runtime.invoke("os.open_path", {"path": "src"}, ctx)
    assert ok.status == ToolStatus.SUCCEEDED
    assert m3.desktop.opened == [(root / "src").resolve()]


async def test_window_metadata_and_unsupported_capabilities(m3: M3, project: Project) -> None:
    ctx = await m3.ctx(project)
    unsupported = await m3.runtime.invoke("window.active", {}, ctx)
    assert unsupported.status == ToolStatus.FAILED  # reported, not crashed
    m3.desktop.window = WindowInfo("main.py - PyCharm", 99, "pycharm64.exe", None, (0, 0, 10, 10))
    ok = await m3.runtime.invoke("window.active", {}, ctx)
    assert ok.output is not None and ok.output["process_name"] == "pycharm64.exe"
    focus = await m3.runtime.invoke("app.focus", {"pid": 99}, ctx)
    assert focus.status == ToolStatus.FAILED
