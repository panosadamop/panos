import os
from pathlib import Path

import pytest

from panos_core.tools.paths import (
    PathPolicyError,
    is_git_internal,
    relative_to_root,
    resolve_in_root,
)

pytestmark = pytest.mark.req("NFR-SEC-001", "FR-004")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "root"
    (r / "src").mkdir(parents=True)
    (r / "src" / "a.txt").write_text("a", encoding="utf-8")
    return r


@pytest.mark.parametrize(
    "raw",
    [
        "src/a.txt",
        "src\\a.txt" if os.name == "nt" else "src/a.txt",
        "./src/../src/a.txt",
        "new/file.txt",
        ".",
    ],
)
def test_paths_inside_root_resolve(root: Path, raw: str) -> None:
    resolved = resolve_in_root(root, raw)
    assert resolved == root.resolve() or root.resolve() in resolved.parents


def test_absolute_path_inside_root_is_ok(root: Path) -> None:
    assert resolve_in_root(root, str(root / "src" / "a.txt")) == (root / "src" / "a.txt").resolve()


@pytest.mark.parametrize("raw", ["..", "../x", "src/../../x", "src/../../../etc/passwd"])
def test_traversal_rejected(root: Path, raw: str) -> None:
    with pytest.raises(PathPolicyError, match="escapes"):
        resolve_in_root(root, raw)


def test_absolute_path_outside_root_rejected(root: Path, tmp_path: Path) -> None:
    with pytest.raises(PathPolicyError, match="escapes"):
        resolve_in_root(root, str(tmp_path / "elsewhere.txt"))


def test_sibling_with_common_prefix_rejected(root: Path, tmp_path: Path) -> None:
    (tmp_path / "root-evil").mkdir()
    with pytest.raises(PathPolicyError, match="escapes"):
        resolve_in_root(root, str(tmp_path / "root-evil" / "x"))


@pytest.mark.parametrize("raw", ["", "   ", "a\x00b"])
def test_empty_and_nul_rejected(root: Path, raw: str) -> None:
    with pytest.raises(PathPolicyError):
        resolve_in_root(root, raw)


# Windows syntax rules are checked on every OS (windows=True) so CI covers them everywhere.
@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("\\\\?\\C:\\x", "namespace"),
        ("\\\\.\\PhysicalDrive0", "namespace"),
        ("//?/C:/x", "namespace"),
        ("C:relative.txt", "drive-relative"),
        ("src\\a.txt:hidden", "alternate data"),
        ("a.txt::$DATA", "alternate data"),
        ("CON", "reserved"),
        ("src\\nul.txt", "reserved"),
        ("COM1.log", "reserved"),
        ("lpt9", "reserved"),
        ("conout$", "reserved"),
        ("src\\evil. ", "trailing"),
        ("trailing.", "trailing"),
    ],
)
def test_windows_syntax_rejections(root: Path, raw: str, reason: str) -> None:
    with pytest.raises(PathPolicyError, match=reason):
        resolve_in_root(root, raw, windows=True)


def test_symlink_escape_rejected(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s", encoding="utf-8")
    link = root / "link"
    try:
        if os.name == "nt":
            # A directory junction needs no privilege on Windows.
            import _winapi

            _winapi.CreateJunction(str(outside), str(link))
        else:
            link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create link on this system")
    with pytest.raises(PathPolicyError, match="escapes"):
        resolve_in_root(root, "link/secret.txt")


def test_relative_and_git_internal_helpers(root: Path) -> None:
    (root / ".git").mkdir()
    assert relative_to_root(root, (root / "src" / "a.txt").resolve()) == "src/a.txt"
    assert relative_to_root(root, root.resolve()) == "."
    assert is_git_internal(root, (root / ".git" / "config").resolve())
    assert is_git_internal(root, (root / ".GIT" / "HEAD").resolve()) or os.name != "nt"
    assert not is_git_internal(root, (root / "src" / "a.txt").resolve())
