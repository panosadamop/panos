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


@pytest.mark.req("US-011", "US-012", "AC-US012-01", "AC-US010-02")
def test_cli_project_register_analyze_index_search_open(
    settings: PanosSettings, node: tuple[subprocess.Popen[str], RuntimeInfo], tmp_path: Path
) -> None:
    """M3 exit criterion: the CLI can register, analyze and open a real project."""
    from tests.repos import copy_repo

    repo = copy_repo("python_fastapi", tmp_path, git=True)
    added = _run_cli("project", "add", str(repo), "--name", "orders")
    assert added.returncode == 0, added.stderr
    assert "LOCAL_ONLY" in added.stdout
    analyzed = _run_cli("project", "analyze", "orders")
    assert analyzed.returncode == 0, analyzed.stderr
    assert "FastAPI" in analyzed.stdout and "uv run pytest" in analyzed.stdout
    indexed = _run_cli("project", "index", "orders")
    assert indexed.returncode == 0 and "indexed" in indexed.stdout
    found = _run_cli("project", "search", "orders", "OrderService")
    assert "src/orders/api.py" in found.stdout
    listed = _run_cli("project", "list")
    assert "orders" in listed.stdout
    # Dry run only: never pop up a real IDE window during tests.
    preview = _run_cli("project", "open", "orders", "--dry-run")
    if preview.returncode == 0:
        assert "would open" in preview.stdout and str(repo.resolve()) in preview.stdout
    else:
        assert "no supported IDE" in preview.stderr  # CI runner without IDEs


def _ollama_models() -> set[str]:
    try:
        r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=2, trust_env=False)
        return {m["name"].removesuffix(":latest") for m in r.json().get("models", [])}
    except (httpx.HTTPError, ValueError):
        return set()


@pytest.fixture
def ollama_config() -> None:
    """Use the machine's real Ollama (skip when it is not running or has no chat model)."""
    if not {"qwen2.5:3b", "qwen2.5-coder:7b"} & _ollama_models():
        pytest.skip("local Ollama with qwen2.5 models is not available")
    Path(os.environ["PANOS_CONFIG_FILE"]).write_text(
        '[[ai.providers]]\nid = "ollama"\nkind = "ollama"\nbase_url = "http://127.0.0.1:11434"\n',
        encoding="utf-8",
    )


@pytest.mark.timeout(300)
@pytest.mark.req("FR-002", "US-024", "NFR-PERF-002")
def test_cli_ask_against_live_node_and_real_local_model(
    ollama_config: None, node: tuple[subprocess.Popen[str], RuntimeInfo]
) -> None:
    providers = _run_cli("ai", "providers")
    assert providers.returncode == 0 and "ollama" in providers.stdout
    fast = _run_cli("ask", "--json", "list projects")
    assert fast.returncode == 0 and json.loads(fast.stdout)["provider"] is None
    result = subprocess.run(
        [*PANOS, "ask", "--json", "Reply with the single word: pong"],
        capture_output=True,
        text=True,
        timeout=280,
        env=os.environ.copy(),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    reply = json.loads(result.stdout)
    assert reply["locality"] == "local" and reply["provider"] == "ollama"
    assert reply["privacy"] == "LOCAL_ONLY" and reply["text"].strip()
