"""End-to-end with real processes (M9-09): a hub, a node, and the CLIs on both sides.

* hub process: ``init_hub`` + ``serve`` in one process (the test-only in-memory secret
  store cannot cross processes; tests never touch the real keyring);
* node process: ``panos serve`` with its own data directories;
* ``panos node join`` (node CLI → node API → hub), ``panos hub nodes`` / ``dispatch`` (hub CLI
  → admin token on loopback), and the job running on the node under the node's policy.
"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.e2e.test_node_daemon import _POPEN_FLAGS, PANOS, _stop

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.timeout(240),
    pytest.mark.req("BR-024", "FR-026", "US-060", "US-061"),
]

HUB_SCRIPT = """
import asyncio, sys
from panos_core.config import load_settings
from panos_core.secrets import InMemorySecretStore
from panos_control_plane.material import init_hub
from panos_control_plane.server import serve
settings = load_settings()
store = InMemorySecretStore()
init_hub(settings, store, hostname="localhost", addresses=["127.0.0.1"], database_url=sys.argv[1])
asyncio.run(serve(settings, store))
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def node_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    home = tmp_path / "node-pc"
    for name, sub in (
        ("DATA_DIR", "data"),
        ("CONFIG_DIR", "config"),
        ("LOG_DIR", "logs"),
        ("RUNTIME_DIR", "run"),
        ("WORKSPACE_DIR", "workspace"),
    ):
        env[f"PANOS_{name}"] = str(home / sub)
    return env


def run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*PANOS, *args],
        capture_output=True,
        text=True,
        timeout=75,
        env=env or os.environ.copy(),
        check=False,
    )


@pytest.fixture
def pg_url(pg_admin_url: str) -> Iterator[str]:
    import asyncio
    import secrets

    import asyncpg

    name = f"e2e_{secrets.token_hex(5)}"

    async def admin(sql: str) -> None:
        conn = await asyncpg.connect(pg_admin_url)
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    asyncio.run(admin(f'CREATE DATABASE "{name}"'))
    base, _, _ = pg_admin_url.rpartition("/")
    yield f"{base}/{name}"
    asyncio.run(admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def wait_until(check: object, limit_s: float = 60.0, what: str = "") -> str:
    deadline = time.monotonic() + limit_s
    last = ""
    while time.monotonic() < deadline:
        ok, last = check()  # type: ignore[operator]
        if ok:
            return str(last)
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}: {last}")


def test_hub_node_join_dispatch_and_revoke_with_real_processes(
    pg_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, link = free_port(), free_port()
    monkeypatch.setenv("PANOS_HUB__API_PORT", str(api))
    monkeypatch.setenv("PANOS_HUB__LINK_PORT", str(link))
    monkeypatch.setenv("PANOS_HUB__BIND_HOST", "127.0.0.1")
    hub_log = (tmp_path / "hub.log").open("w", encoding="utf-8")
    hub = subprocess.Popen(
        [sys.executable, "-c", HUB_SCRIPT, pg_url],
        env=os.environ.copy(),
        stdout=hub_log,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=_POPEN_FLAGS,
    )
    nenv = node_env(tmp_path)
    # Logs go to files: an unread pipe fills up and blocks the process writing to it.
    node_log = (tmp_path / "node.log").open("w", encoding="utf-8")
    node = subprocess.Popen(
        [*PANOS, "serve"],
        env=nenv,
        stdout=node_log,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=_POPEN_FLAGS,
    )
    try:

        def hub_up() -> tuple[bool, str]:
            assert hub.poll() is None, (tmp_path / "hub.log").read_text(encoding="utf-8")
            r = run(["hub", "status"])
            return r.returncode == 0, r.stdout + r.stderr

        wait_until(hub_up, what="hub")
        code_out = run(["hub", "enroll-code", "--name", "build-pc"]).stdout
        code = code_out.split("--code ")[1].split()[0]
        fingerprint = code_out.split("--fingerprint ")[1].split()[0]

        def node_up() -> tuple[bool, str]:
            assert node.poll() is None, (tmp_path / "node.log").read_text(encoding="utf-8")
            r = run(["status"], nenv)
            return r.returncode == 0, r.stdout

        wait_until(node_up, what="node")
        joined = run(
            [
                "node",
                "join",
                f"https://localhost:{api}",
                "--code",
                code,
                "--fingerprint",
                fingerprint,
                "--name",
                "build-pc",
            ],
            nenv,
        )
        assert joined.returncode == 0, joined.stdout + joined.stderr
        wait_until(
            lambda: ("link connected" in (s := run(["node", "status"], nenv).stdout), s),
            what="link",
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "hello.py").write_bytes(b"print('hi')\n")
        assert run(["project", "add", str(repo)], nenv).returncode == 0
        wait_until(
            lambda: ("1 project(s)" in (s := run(["hub", "nodes"]).stdout), s), what="inventory"
        )

        sent = run(
            [
                "hub",
                "dispatch",
                "build-pc",
                "Summarise",
                "the",
                "repo",
                "-p",
                "repo",
                "--kind",
                "research",
            ]
        )
        # A research job on a LOCAL_ONLY project is accepted by the node, then refused web
        # access by its own policy (ADR-027): proof that the target's rules decide.
        assert "accepted it as job" in sent.stdout, sent.stdout + sent.stderr
        job_id = sent.stdout.split("as job ")[1].strip()
        shown = wait_until(
            lambda: (
                any(
                    w in (s := run(["job", "show", job_id], nenv).stdout)
                    for w in ("BLOCKED", "COMPLETED")
                ),
                s,
            ),
            what="job end",
        )
        assert "BLOCKED" in shown

        refused = run(["hub", "dispatch", "build-pc", "Do", "it", "-p", "missing"])
        assert refused.returncode != 0 and "no project" in refused.stderr

        revoked = run(["hub", "revoke", "build-pc"])
        assert revoked.returncode == 0, (
            revoked.stderr + (tmp_path / "hub.log").read_text(encoding="utf-8")[-3000:]
        )
        wait_until(
            lambda: ("revoked" in (s := run(["node", "status"], nenv).stdout), s), what="revocation"
        )
    finally:
        hub_log.close()
        node_log.close()
        _stop(node)
        _stop(hub)
        for p in (node, hub):
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()
