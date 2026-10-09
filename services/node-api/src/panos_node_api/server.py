"""Run the node daemon (ADR-002/ADR-003).

1. Acquire the per-user single-instance lock.
2. Bind a loopback socket on a random free port (no bind race: uvicorn reuses it).
3. Generate a fresh bearer token and write the owner-only runtime file.
4. Serve until stopped, then remove the runtime file and release the lock.
"""

import asyncio
import os
import socket

import uvicorn
from filelock import FileLock, Timeout

from panos_core import __version__
from panos_core.config import PanosSettings, ensure_directories
from panos_core.config.runtime import generate_token, remove_runtime_file, write_runtime_file
from panos_core.observability import configure_logging, get_logger
from panos_node_api.app import create_app
from panos_node_api.security import AuthConfig

LOOPBACK = "127.0.0.1"

log = get_logger(__name__)


class AlreadyRunningError(RuntimeError):
    """Another node instance holds the lock for this user."""


def bind_loopback_socket(port: int = 0) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name != "nt":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((LOOPBACK, port))
    sock.listen(128)
    sock.setblocking(False)
    return sock


def serve(settings: PanosSettings, *, port: int = 0) -> None:
    """Run the node in the foreground. Raises :class:`AlreadyRunningError`."""
    ensure_directories(settings)
    configure_logging(
        level=settings.log_level, log_dir=settings.log_dir, json_console=settings.log_json_console
    )
    lock = FileLock(str(settings.lock_file))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise AlreadyRunningError(
            f"a Panos node is already running ({settings.lock_file})"
        ) from exc

    stopped = False

    def cleanup() -> None:
        # Idempotent: called from the app lifespan and again from ``finally``.
        nonlocal stopped
        if stopped:
            return
        stopped = True
        remove_runtime_file(settings.runtime_file, expected_pid=os.getpid())
        log.info("node_stopped")

    sock = None
    try:
        sock = bind_loopback_socket(port)
        bound_port = sock.getsockname()[1]
        token = generate_token()
        auth = AuthConfig.for_loopback(token, bound_port, settings.allowed_origins)
        app = create_app(settings, auth, on_shutdown=cleanup)
        write_runtime_file(
            settings.runtime_file, host=LOOPBACK, port=bound_port, token=token, version=__version__
        )
        log.info(
            "node_listening",
            host=LOOPBACK,
            port=bound_port,
            runtime_file=str(settings.runtime_file),
        )
        config = uvicorn.Config(
            app, log_config=None, access_log=False, lifespan="on", server_header=False
        )
        asyncio.run(uvicorn.Server(config).serve(sockets=[sock]))
    finally:
        cleanup()
        if sock is not None:
            sock.close()
        lock.release()
