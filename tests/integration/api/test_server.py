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


@pytest.mark.req("NFR-SEC-001")
def test_dev_origin_is_allowed_only_in_dev_mode(settings: PanosSettings) -> None:
    from panos_core.config.settings import DEFAULT_ALLOWED_ORIGINS, DEV_ALLOWED_ORIGINS
    from panos_node_api.server import effective_origins

    assert effective_origins(settings) == DEFAULT_ALLOWED_ORIGINS
    dev = effective_origins(settings, dev=True)
    assert dev == (*DEFAULT_ALLOWED_ORIGINS, *DEV_ALLOWED_ORIGINS)
    # Idempotent when the origin is already configured.
    configured = settings.model_copy(update={"allowed_origins": dev})
    assert effective_origins(configured, dev=True) == dev


@pytest.mark.req("NFR-SEC-001")
def test_dev_origin_reaches_the_api_only_in_dev_mode(settings: PanosSettings) -> None:
    from fastapi.testclient import TestClient

    from panos_node_api.app import create_app
    from panos_node_api.security import AuthConfig
    from panos_node_api.server import effective_origins

    port, token = 48123, "t" * 32
    headers = {"Authorization": f"Bearer {token}", "Origin": "http://localhost:5173"}
    for dev, expected in ((False, 403), (True, 200)):
        auth = AuthConfig.for_loopback(token, port, effective_origins(settings, dev=dev))
        with TestClient(create_app(settings, auth), base_url=f"http://127.0.0.1:{port}") as client:
            assert client.get("/api/v1/health", headers=headers).status_code == expected
