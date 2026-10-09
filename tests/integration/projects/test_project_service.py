"""Project registry (FR-004, BR-029, US-011, AC-US011-01..03, AC-US005-01/05)."""

import os
from pathlib import Path

import pytest

from panos_core.config import PanosSettings
from panos_core.domain import PermissionLevel, PrivacyMode
from panos_core.projects import ProjectError
from tests.integration.projects.conftest import M3

pytestmark = pytest.mark.req("FR-004", "US-011")


@pytest.mark.req("AC-US011-01", "AC-US011-02", "AC-US005-01", "FR-028")
async def test_register_existing_directory(m3: M3, tmp_path: Path) -> None:
    repo = tmp_path / "billing"
    repo.mkdir()
    project = await m3.projects.register(repo, preferred_ide="jetbrains.pycharm")
    assert project.name == "billing"
    assert Path(project.path) == repo.resolve()
    assert project.privacy_mode == PrivacyMode.LOCAL_ONLY
    assert project.preferred_ide == "jetbrains.pycharm"
    events = await m3.audit.list(event_type="project.registered")
    assert events[0].project_id == project.id


@pytest.mark.req("BR-029", "AC-US011-01")
async def test_create_in_workspace(m3: M3, settings: PanosSettings) -> None:
    project = await m3.projects.create("new-app")
    assert Path(project.path) == (settings.workspace_dir / "new-app").resolve()
    assert Path(project.path).is_dir()


@pytest.mark.req("AC-US011-03")
async def test_duplicates_and_missing_paths_rejected(m3: M3, tmp_path: Path) -> None:
    repo = tmp_path / "x"
    repo.mkdir()
    await m3.projects.register(repo)
    with pytest.raises(ProjectError) as err:
        await m3.projects.register(repo)
    assert err.value.reason == "duplicate"
    other = tmp_path / "y"
    other.mkdir()
    with pytest.raises(ProjectError) as err:
        await m3.projects.register(other, name="X")  # case-insensitive name clash
    assert err.value.reason == "duplicate"
    with pytest.raises(ProjectError) as err:
        await m3.projects.register(tmp_path / "missing")
    assert err.value.reason == "not_found"
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(ProjectError) as err:
        await m3.projects.register(file_path)
    assert err.value.reason == "invalid_path"


@pytest.mark.req("NFR-SEC-001", "AC-US011-03")
async def test_dangerously_broad_locations_are_refused(m3: M3, settings: PanosSettings) -> None:
    anchor = Path(Path.cwd().anchor)
    for path in (anchor, Path.home(), settings.data_dir):
        with pytest.raises(ProjectError) as err:
            await m3.projects.register(path)
        assert err.value.reason in ("too_broad", "forbidden_location")
    inner = settings.data_dir / "inside"
    inner.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ProjectError) as err:
        await m3.projects.register(inner)
    assert err.value.reason == "forbidden_location"
    if os.name == "nt" and os.environ.get("SYSTEMROOT"):
        with pytest.raises(ProjectError):
            await m3.projects.register(Path(os.environ["SYSTEMROOT"]))


async def test_invalid_names_rejected(m3: M3, tmp_path: Path) -> None:
    repo = tmp_path / "ok"
    repo.mkdir()
    for name in ("../evil", "a/b", " lead", "x" * 101):
        with pytest.raises(ProjectError):
            await m3.projects.register(repo, name=name)
    with pytest.raises(ProjectError):
        await m3.projects.create("..")


async def test_lookup_by_id_or_name(m3: M3, tmp_path: Path) -> None:
    repo = tmp_path / "Lookup"
    repo.mkdir()
    project = await m3.projects.register(repo)
    assert (await m3.projects.get(project.id)).id == project.id
    assert (await m3.projects.get("lookup")).id == project.id
    with pytest.raises(ProjectError):
        await m3.projects.get("nope")


@pytest.mark.req("AC-US005-05", "FR-028")
async def test_privacy_change_is_explicit_and_audited(m3: M3, tmp_path: Path) -> None:
    repo = tmp_path / "p"
    repo.mkdir()
    project = await m3.projects.register(repo)
    updated = await m3.projects.set_privacy_mode(project.id, PrivacyMode.STANDARD, actor="user")
    assert updated.privacy_mode == PrivacyMode.STANDARD
    event = (await m3.audit.list(event_type="project.privacy_changed"))[0]
    assert event.metadata == {"from": "LOCAL_ONLY", "to": "STANDARD"}
    assert event.actor == "user"


@pytest.mark.req("NFR-SEC-001")
async def test_policy_overrides_tighten_and_refuse_illegal_loosening(
    m3: M3, tmp_path: Path
) -> None:
    repo = tmp_path / "p"
    repo.mkdir()
    project = await m3.projects.register(repo)
    tightened = await m3.projects.set_policy_override(
        project.id, "fs.write", PermissionLevel.P3_APPROVAL_REQUIRED, actor="user"
    )
    assert tightened.policy_overrides == {"fs.write": PermissionLevel.P3_APPROVAL_REQUIRED}
    loosened = await m3.projects.set_policy_override(
        project.id, "fs.delete", PermissionLevel.P1_SAFE_WRITE, actor="user"
    )
    assert loosened.policy_overrides["fs.delete"] == PermissionLevel.P1_SAFE_WRITE
    for action, level in (
        ("git.commit", PermissionLevel.P1_SAFE_WRITE),
        ("email.send", PermissionLevel.P0_READ),
        ("pkg.install", PermissionLevel.P1_SAFE_WRITE),
    ):
        with pytest.raises(ProjectError) as err:
            await m3.projects.set_policy_override(project.id, action, level, actor="user")
        assert err.value.reason == "cannot_loosen"
    with pytest.raises(ProjectError):
        await m3.projects.set_policy_override(project.id, "made.up", None, actor="user")
    cleared = await m3.projects.set_policy_override(project.id, "fs.write", None, actor="user")
    assert "fs.write" not in cleared.policy_overrides
    # Persisted overrides reach the tool context.
    ctx = await m3.ctx(await m3.projects.get(project.id))
    assert ctx.policy_overrides == {"fs.delete": PermissionLevel.P1_SAFE_WRITE}
