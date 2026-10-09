"""End-to-end: real ``panos serve`` process, CLI status, single instance, clean shutdown."""

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from panos_core.config import PanosSettings
from panos_core.config.runtime import RuntimeInfo, read_runtime_file
from panos_core.platform.secure_file import is_owner_only

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]

PANOS = [sys.executable, "-m", "panos_cli.main"]
_POPEN_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*PANOS, *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=os.environ.copy(),
        check=False,
    )


def _wait_for_runtime(settings: PanosSettings, proc: subprocess.Popen[str]) -> RuntimeInfo:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"node exited early:\n{proc.stderr.read() if proc.stderr else ''}")
        # Note: on Windows a venv python.exe is a launcher, so the node's own PID
        # (recorded in the runtime file) differs from proc.pid.
        info = read_runtime_file(settings.runtime_file)
        if info is not None:
            try:
                httpx.get(f"{info.base_url}/api/v1/health", timeout=2, trust_env=False)
                return info
            except httpx.TransportError:
                pass
        time.sleep(0.2)
    raise AssertionError("node did not start within 60s")


def _stop(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


@pytest.fixture
def node(settings: PanosSettings) -> Iterator[tuple[subprocess.Popen[str], RuntimeInfo]]:
    proc = subprocess.Popen(
        [*PANOS, "serve"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
        creationflags=_POPEN_FLAGS,
    )
    try:
        yield proc, _wait_for_runtime(settings, proc)
    finally:
        _stop(proc)


@pytest.mark.req("BR-001", "NFR-SEC-001")
def test_serve_status_and_authenticated_health(
    settings: PanosSettings, node: tuple[subprocess.Popen[str], RuntimeInfo]
) -> None:
    _proc, info = node
    assert info.host == "127.0.0.1"
    assert is_owner_only(settings.runtime_file)

    status = _run_cli("status", "--json")
    assert status.returncode == 0, status.stderr
    data = json.loads(status.stdout)
    assert data["running"] is True and data["status"] == "ok"

    unauth = httpx.get(f"{info.base_url}/api/v1/health", trust_env=False)
    assert unauth.status_code == 401
    ok = httpx.get(
        f"{info.base_url}/api/v1/health",
        trust_env=False,
        headers={"Authorization": f"Bearer {info.token}"},
    )
    assert ok.status_code == 200


def test_second_instance_refused(
    settings: PanosSettings, node: tuple[subprocess.Popen[str], RuntimeInfo]
) -> None:
    first = node[1]
    second = _run_cli("serve")
    assert second.returncode == 3
    assert "already running" in second.stderr
    # The first node is unaffected and still owns the runtime file.
    info = read_runtime_file(settings.runtime_file)
    assert info is not None and (info.pid, info.token) == (first.pid, first.token)


def test_graceful_stop_removes_runtime_file(
    settings: PanosSettings, node: tuple[subprocess.Popen[str], RuntimeInfo]
) -> None:
    proc, _info = node
    _stop(proc)
    assert proc.returncode is not None
    assert not settings.runtime_file.exists()
    assert _run_cli("status").returncode == 1


def test_node_writes_structured_log_file(
    settings: PanosSettings, node: tuple[subprocess.Popen[str], RuntimeInfo]
) -> None:
    _proc, info = node
    log_file = Path(settings.log_dir) / "panos.log"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (log_file.exists() and log_file.stat().st_size):
        time.sleep(0.2)
    events = [json.loads(line)["event"] for line in log_file.read_text("utf-8").splitlines()]
    assert "node_listening" in events and "node_ready" in events
    assert info.token not in log_file.read_text("utf-8")


@pytest.mark.req("FR-028")
def test_cli_audit_and_approvals_against_live_node(
    settings: PanosSettings, node: tuple[subprocess.Popen[str], RuntimeInfo]
) -> None:
    verify = _run_cli("audit", "verify")
    assert verify.returncode == 0, verify.stderr
    assert "audit chain OK" in verify.stdout
    pending = _run_cli("approvals", "list")
    assert pending.returncode == 0
    assert "no pending approvals" in pending.stdout
