"""End-to-end: a job survives a hard kill of the node (ADR-011, NFR-REL-002, AC-US050-01)."""

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import psutil
import pytest

from panos_core.config import PanosSettings
from panos_core.config.runtime import RuntimeInfo
from tests.e2e.test_node_daemon import _POPEN_FLAGS, PANOS, _run_cli, _stop, _wait_for_runtime
from tests.repos import copy_repo

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(180)]


def _start(settings: PanosSettings) -> tuple[subprocess.Popen[str], RuntimeInfo]:
    proc = subprocess.Popen(
        [*PANOS, "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        env=os.environ.copy(),
        creationflags=_POPEN_FLAGS,
    )
    return proc, _wait_for_runtime(settings, proc)


def _api(info: RuntimeInfo) -> httpx.Client:
    return httpx.Client(
        base_url=f"{info.base_url}/api/v1",
        trust_env=False,
        timeout=30,
        headers={"Authorization": f"Bearer {info.token}"},
    )


def _job(client: httpx.Client, job_id: str) -> dict[str, Any]:
    data: dict[str, Any] = client.get(f"/jobs/{job_id}").raise_for_status().json()
    return data


def _node_pid(info: RuntimeInfo) -> int:
    return info.pid  # the real node (a venv python.exe is only a launcher)


@pytest.mark.req("AC-US050-01", "NFR-REL-002", "NFR-REL-001", "FR-022")
def test_hard_killed_node_recovers_job_as_paused(settings: PanosSettings, tmp_path: Path) -> None:
    root = copy_repo("calc_unittest", tmp_path)
    (root / "sleeper.py").write_text(
        "import subprocess, sys, time, pathlib\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "pathlib.Path('child.pid').write_text(str(child.pid))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    proc, info = _start(settings)
    try:
        assert _run_cli("project", "add", str(root)).returncode == 0
        with _api(info) as api:
            job = api.post(
                "/jobs",
                json={
                    "objective": "long command",
                    "kind": "command",
                    "project": "calc_unittest",
                    "argv": [sys.executable, "sleeper.py"],
                },
            ).json()
            deadline = time.monotonic() + 30
            while _job(api, job["id"])["status"] != "WAITING_FOR_APPROVAL":
                assert time.monotonic() < deadline
                time.sleep(0.2)
            approval = _job(api, job["id"])["pending_approval"]
            api.post(
                f"/approvals/{approval}/decision", json={"decision": "approve"}
            ).raise_for_status()
            while not (root / "child.pid").exists():
                assert time.monotonic() < deadline + 30, "command did not start"
                time.sleep(0.2)
            assert _job(api, job["id"])["status"] == "RUNNING"
        child = int((root / "child.pid").read_text())

        # Crash: terminate the node process abruptly (no shutdown handlers run).
        psutil.Process(_node_pid(info)).kill()
        proc.wait(timeout=30)
        if sys.platform == "win32":  # Job Object KILL_ON_JOB_CLOSE takes the tools down too
            deadline = time.monotonic() + 15
            while psutil.pid_exists(child):
                assert time.monotonic() < deadline, "tool process outlived the node"
                time.sleep(0.2)
    finally:
        _stop(proc)

    proc, info = _start(settings)
    try:
        with _api(info) as api:
            recovered = _job(api, job["id"])
            assert recovered["status"] == "PAUSED"
            assert recovered["status_reason"] == "interrupted"
            steps = recovered["steps"]
            assert steps and steps[-1]["status"] == "RUNNING"  # persisted before it ran
            cancelled = api.post(f"/jobs/{job['id']}/cancel").json()
            assert cancelled["status"] == "CANCELLED"
    finally:
        _stop(proc)
