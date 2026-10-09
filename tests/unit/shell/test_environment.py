import os
from pathlib import Path

import pytest

from panos_core.shell.environment import (
    batch_argument_problem,
    build_child_env,
    is_sensitive_env,
    resolve_executable,
)

pytestmark = pytest.mark.req("NFR-SEC-004")


@pytest.mark.parametrize(
    "name",
    [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "DB_PASSWORD",
        "MY_SERVICE_KEY",
        "PANOS_TOKEN",
        "NPM_TOKEN",
        "AZURE_CLIENT_SECRET",
        "GH_PAT",
    ],
)
def test_sensitive_names(name: str) -> None:
    assert is_sensitive_env(name)


@pytest.mark.parametrize(
    "name",
    [
        "PATH",
        "HOME",
        "JAVA_HOME",
        "LANG",
        "TEMP",
        "SYSTEMROOT",
        "NUMBER_OF_PROCESSORS",
        "VIRTUAL_ENV",
    ],
)
def test_ordinary_names(name: str) -> None:
    assert not is_sensitive_env(name)


def test_child_env_drops_secrets_and_applies_overrides() -> None:
    base = {"PATH": "/bin", "OPENAI_API_KEY": "sk-x", "HOME": "/h", "GITHUB_TOKEN": "t"}
    env, removed = build_child_env(base, {"CI": "1"})
    assert env["PATH"] == "/bin" and env["CI"] == "1"
    assert "OPENAI_API_KEY" not in env and "GITHUB_TOKEN" not in env
    assert removed == ["GITHUB_TOKEN", "OPENAI_API_KEY"]
    if os.name == "nt":
        assert env["NoDefaultCurrentDirectoryInExePath"] == "1"


@pytest.mark.parametrize(
    "overrides", [{"API_TOKEN": "x"}, {"1BAD": "x"}, {"A B": "x"}, {"OK": "a\x00b"}]
)
def test_bad_overrides_rejected(overrides: dict[str, str]) -> None:
    with pytest.raises(ValueError, match=r"(invalid|sensitive|NUL)"):
        build_child_env({}, overrides)


def test_resolve_bare_name_uses_path_not_cwd(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    name = "planted.exe" if os.name == "nt" else "planted"
    planted = project / name
    planted.write_text("x", encoding="utf-8")
    planted.chmod(0o755)
    # Present in cwd, absent from PATH → not found (no implicit current-directory lookup).
    assert resolve_executable("planted", "", cwd=project, root=project) is None
    bindir = tmp_path / "bin"
    bindir.mkdir()
    real = bindir / name
    real.write_text("x", encoding="utf-8")
    real.chmod(0o755)
    assert resolve_executable("planted", str(bindir), cwd=project, root=project) == real


def test_relative_executable_must_stay_in_project(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / ("tool.exe" if os.name == "nt" else "tool")
    outside.write_text("x", encoding="utf-8")
    outside.chmod(0o755)
    assert resolve_executable("../tool", "", cwd=project, root=project) is None
    local = project / ("gradlew.bat" if os.name == "nt" else "gradlew")
    local.write_text("x", encoding="utf-8")
    local.chmod(0o755)
    assert resolve_executable("./gradlew", "", cwd=project, root=project) == local.resolve()


@pytest.mark.parametrize("arg", ["a&b", "x|y", "^z", "%PATH%", 'q"q', "line\nbreak", "!v!"])
def test_batch_files_refuse_metacharacters(arg: str) -> None:
    assert batch_argument_problem(Path("build.bat"), ["ok", arg]) is not None
    assert batch_argument_problem(Path("build.CMD"), [arg]) is not None


def test_non_batch_targets_accept_any_argument() -> None:
    assert batch_argument_problem(Path("git.exe"), ["a&b", "%X%"]) is None
    assert batch_argument_problem(Path("build.bat"), ["--flag", "value"]) is None
