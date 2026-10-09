import pytest
from filelock import FileLock

from panos_core.config import PanosSettings
from panos_node_api.server import LOOPBACK, AlreadyRunningError, bind_loopback_socket, serve


def test_bind_loopback_socket_uses_random_loopback_port() -> None:
    sock = bind_loopback_socket()
    try:
        host, port = sock.getsockname()
        assert host == LOOPBACK
        assert port > 0
    finally:
        sock.close()


def test_serve_refuses_when_another_instance_holds_the_lock(settings: PanosSettings) -> None:
    with (
        FileLock(str(settings.lock_file)),
        pytest.raises(AlreadyRunningError, match="already running"),
    ):
        serve(settings)
    # The refused attempt must not have written or removed a runtime file.
    assert not settings.runtime_file.exists()
