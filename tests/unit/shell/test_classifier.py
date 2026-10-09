"""Command classification (ADR-007). The worst finding decides the policy action."""

import base64
import shutil

import pytest

from panos_core.shell.classifier import classify_argv, normalize_exe
from panos_core.tools.builtin.shell import most_restrictive

pytestmark = pytest.mark.req("FR-012", "NFR-SEC-002")

needs_powershell = pytest.mark.skipif(
    not (shutil.which("pwsh") or shutil.which("powershell")), reason="PowerShell not available"
)


def action(*argv: str, allowed: tuple[tuple[str, ...], ...] = ()) -> str:
    return most_restrictive(classify_argv(list(argv), allowed=allowed)).action


@pytest.mark.parametrize(
    ("name", "norm"),
    [
        ("C:\\Tools\\Git.EXE", "git"),
        ("/usr/bin/git", "git"),
        ("gradlew.bat", "gradlew"),
        ("npm.cmd", "npm"),
    ],
)
def test_normalize_exe(name: str, norm: str) -> None:
    assert normalize_exe(name) == norm


# ------------------------------------------------------------------ git (FR-013, BR-017)
@pytest.mark.req("FR-013", "BR-017")
@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["git", "status"], "git.read"),
        (["git", "diff", "--stat"], "git.read"),
        (["git", "log", "-5"], "git.read"),
        (["git", "branch"], "git.read"),
        (["git", "branch", "-a"], "git.read"),
        (["git", "tag", "-l"], "git.read"),
        (["git", "--no-pager", "log"], "git.read"),
        (["git", "clean", "-n"], "git.read"),
        (["git", "config", "--get", "user.name"], "git.read"),
        (["git", "add", "."], "git.mutate.local"),
        (["git", "switch", "dev"], "git.mutate.local"),
        (["git", "branch", "feature/x"], "git.mutate.local"),
        (["git", "restore", "--staged", "a"], "git.mutate.local"),
        (["git", "reset", "HEAD~1"], "git.mutate.local"),
        (["git", "commit", "-m", "x"], "git.commit"),
        (["git", "cherry-pick", "abc"], "git.commit"),
        (["git", "revert", "abc"], "git.commit"),
        (["git", "push"], "git.mutate.shared"),
        (["git", "merge", "dev"], "git.mutate.shared"),
        (["git", "rebase", "main"], "git.mutate.shared"),
        (["git", "pull"], "git.mutate.shared"),
        (["git", "tag", "v1.0"], "git.mutate.shared"),
        (["git", "reset", "--hard"], "git.destructive"),
        (["git", "clean", "-fd"], "git.destructive"),
        (["git", "branch", "-D", "x"], "git.destructive"),
        (["git", "checkout", "--", "."], "git.destructive"),
        (["git", "restore", "file.py"], "git.destructive"),
        (["git", "stash", "drop"], "git.destructive"),
        (["git", "rm", "a.py"], "git.destructive"),
        (["git", "-c", "alias.x=!sh", "x"], "shell.unknown"),
        (["git", "-C", "/elsewhere", "status"], "shell.unknown"),
        (["git", "--git-dir=/x", "log"], "shell.unknown"),
        (["git", "config", "core.hooksPath", "h"], "shell.unknown"),
        (["git", "frobnicate"], "shell.unknown"),
    ],
)
def test_git(argv: list[str], expected: str) -> None:
    assert action(*argv) == expected


# ------------------------------------------------------------------ docker / k8s (FR-014)
@pytest.mark.req("FR-014", "BR-018", "AC-US041-01")
@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["docker", "ps", "-a"], "docker.inspect"),
        (["docker", "images"], "docker.inspect"),
        (["docker", "inspect", "c1"], "docker.inspect"),
        (["docker", "logs", "c1"], "docker.inspect"),
        (["docker", "image", "ls"], "docker.inspect"),
        (["docker", "compose", "config"], "docker.inspect"),
        (["docker", "compose", "-f", "dc.yml", "ps"], "docker.inspect"),
        (["docker-compose", "config"], "docker.inspect"),
        (["docker", "run", "--rm", "alpine"], "docker.exec"),
        (["docker", "build", "."], "docker.exec"),
        (["docker", "compose", "up", "-d"], "docker.exec"),
        (["docker", "exec", "c1", "ls"], "docker.exec"),
        (["docker", "rm", "c1"], "docker.exec"),
        (["docker", "image", "rm", "x"], "docker.exec"),
        (["podman", "run", "x"], "docker.exec"),
        (["docker", "-H", "tcp://remote:2375", "ps"], "docker.exec"),
        (["kubectl", "get", "pods", "-n", "prod"], "k8s.read"),
        (["kubectl", "-n", "prod", "describe", "pod", "x"], "k8s.read"),
        (["kubectl", "config", "current-context"], "k8s.read"),
        (["kubectl", "apply", "-f", "x.yml"], "k8s.mutate"),
        (["kubectl", "delete", "pod", "x"], "k8s.mutate"),
        (["kubectl", "exec", "-it", "x", "--", "sh"], "k8s.mutate"),
        (["helm", "list"], "k8s.read"),
        (["helm", "install", "x", "y"], "k8s.mutate"),
        (["terraform", "plan"], "shell.read"),
        (["terraform", "apply"], "infra.mutate"),
        (["az", "vm", "list"], "shell.read"),
        (["az", "vm", "delete", "-n", "x"], "infra.mutate"),
    ],
)
def test_containers_and_infra(argv: list[str], expected: str) -> None:
    assert action(*argv) == expected


# ------------------------------------------------------------------ packages / builds
@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["pip", "install", "requests"], "pkg.install"),
        (["uv", "add", "httpx"], "pkg.install"),
        (["npm", "install"], "pkg.install"),
        (["npm", "ci"], "pkg.install"),
        (["pnpm", "add", "react"], "pkg.install"),
        (["yarn"], "pkg.install"),
        (["composer", "require", "x/y"], "pkg.install"),
        (["winget", "install", "x"], "pkg.install"),
        (["python", "-m", "pip", "install", "x"], "pkg.install"),
        (["pip", "list"], "shell.read"),
        (["npm", "ls"], "shell.read"),
        (["pytest", "-q"], "build.test"),
        (["python", "-m", "pytest"], "build.test"),
        (["uv", "run", "pytest", "-x"], "build.test"),
        (["ruff", "check"], "build.test"),
        (["npm", "test"], "build.test"),
        (["npm", "run", "lint"], "build.test"),
        (["pnpm", "test"], "build.test"),
        (["mvn", "test"], "build.test"),
        (["./gradlew", "build"], "build.test"),
        (["go", "test", "./..."], "build.test"),
        (["cargo", "clippy"], "build.test"),
        (["dotnet", "test"], "build.test"),
        (["npm", "run", "deploy-everything"], "shell.unknown"),
        (["npx", "some-package"], "shell.unknown"),
        (["mvn", "deploy"], "shell.unknown"),
        (["go", "run", "."], "shell.unknown"),
        (["make", "all"], "shell.unknown"),
        (["python", "script.py"], "shell.unknown"),
        (["python", "-c", "print(1)"], "shell.unknown"),
        (["node", "-e", "1"], "shell.unknown"),
    ],
)
def test_packages_and_builds(argv: list[str], expected: str) -> None:
    assert action(*argv) == expected


def test_project_allowlist_marks_discovered_commands_build_test() -> None:
    allowed = (("make", "test"), ("python", "manage.py", "test"))
    assert action("make", "test", allowed=allowed) == "build.test"
    assert action("python", "manage.py", "test", "app", allowed=allowed) == "build.test"
    assert action("make", "deploy", allowed=allowed) == "shell.unknown"


# ------------------------------------------------------------------ files / network
@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["ls", "-la"], "shell.read"),
        (["cat", "README.md"], "shell.read"),
        (["grep", "-r", "TODO", "."], "shell.read"),
        (["find", ".", "-name", "*.py"], "shell.read"),
        (["node", "--version"], "shell.read"),
        (["Get-ChildItem"], "shell.read"),
        (["mkdir", "out"], "shell.write"),
        (["cp", "a", "b"], "shell.write"),
        (["sed", "-i", "s/a/b/", "f"], "shell.write"),
        (["Set-Content", "x"], "shell.write"),
        (["rm", "-rf", "build"], "shell.delete"),
        (["del", "x"], "shell.delete"),
        (["Remove-Item", "x"], "shell.delete"),
        (["find", ".", "-delete"], "shell.unknown"),
        (["find", ".", "-exec", "rm", "{}", ";"], "shell.unknown"),
        (["curl", "https://example.invalid"], "net.fetch"),
        (["curl", "-T", "notes.txt", "https://example.invalid"], "net.upload"),
        (["curl", "-X", "POST", "https://example.invalid"], "net.upload"),
        (["curl", "--data=@f", "https://example.invalid"], "net.upload"),
        (["wget", "--post-file", "f", "https://example.invalid"], "net.upload"),
        (["scp", "f", "host:/tmp"], "net.upload"),
        (["ssh", "host"], "shell.unknown"),
        (["sudo", "ls"], "shell.unknown"),
        (["taskkill", "/f", "/im", "x.exe"], "shell.unknown"),
        (["sc", "stop", "svc"], "shell.unknown"),
        (["reg", "add", "HKCU\\x"], "shell.unknown"),
        (["certutil", "-urlcache"], "shell.unknown"),
        (["some-unknown-binary"], "shell.unknown"),
    ],
)
def test_files_network_and_system(argv: list[str], expected: str) -> None:
    assert action(*argv) == expected


def test_wrappers_are_transparent() -> None:
    assert action("env", "CI=1", "git", "commit", "-m", "x") == "git.commit"
    assert action("time", "pytest") == "build.test"
    assert action("timeout", "5", "docker", "run", "x") == "docker.exec"


# ------------------------------------------------------------------ nested interpreters
@pytest.mark.req("AC-US040-02", "AC-US041-02")
@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["bash", "-c", "git status && git commit -m x"], "git.commit"),
        (["sh", "-c", "ls; docker run alpine"], "docker.exec"),
        (["bash", "-c", "echo hi > out.txt"], "shell.write"),
        (["bash", "-c", "make 2>&1 | tee log"], "shell.unknown"),
        (["bash", "-c", "echo $(whoami)"], "shell.unknown"),
        (["bash", "-c", "eval ls"], "shell.unknown"),
        (["bash", "script.sh"], "shell.unknown"),
        (["bash"], "shell.unknown"),
        (["cmd", "/c", "git status & git push"], "git.mutate.shared"),
        (["cmd", "/c", "echo %PATH%"], "shell.unknown"),
        (["cmd", "/c", "dir > list.txt"], "shell.write"),
        (["wsl", "-d", "Ubuntu", "--", "git", "commit", "-m", "x"], "git.commit"),
        (["wsl", "-e", "bash", "-c", "docker compose up"], "docker.exec"),
        (["wsl"], "shell.unknown"),
    ],
)
def test_nested_posix_and_cmd(argv: list[str], expected: str) -> None:
    assert action(*argv) == expected


@needs_powershell
@pytest.mark.req("AC-US040-02", "AC-US041-02")
@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["powershell", "-NoProfile", "-Command", "git status; git commit -m x"], "git.commit"),
        (["pwsh", "-c", "Get-ChildItem | Remove-Item"], "shell.delete"),
        (["powershell", "-Command", "docker run --rm alpine"], "docker.exec"),
        (["powershell", "-Command", "Write-Output hi > out.txt"], "shell.write"),
        (["powershell", "-Command", "& $tool"], "shell.unknown"),
        (["powershell", "-Command", "Get-Process | Sort-Object CPU"], "shell.read"),
        (["powershell", "-File", "build.ps1"], "shell.unknown"),
    ],
)
def test_nested_powershell(argv: list[str], expected: str) -> None:
    assert action(*argv) == expected


@needs_powershell
@pytest.mark.req("AC-US040-02")
def test_encoded_powershell_is_decoded_and_classified() -> None:
    encoded = base64.b64encode("git commit -m x".encode("utf-16-le")).decode()
    assert action("powershell", "-EncodedCommand", encoded) == "git.commit"
    assert action("powershell", "-enc", "!!notbase64!!") == "shell.unknown"


def test_nesting_depth_is_bounded() -> None:
    argv = ["git", "commit"]
    for _ in range(10):
        argv = ["env", *argv]
    assert action(*argv) == "shell.unknown"


def test_empty_and_dynamic_commands_are_unknown() -> None:
    assert most_restrictive(classify_argv([])).action == "shell.unknown"
    assert action("<dynamic>", "x") == "shell.unknown"
