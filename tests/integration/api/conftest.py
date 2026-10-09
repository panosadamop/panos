from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from panos_core.config import PanosSettings
from panos_node_api import create_app
from panos_node_api.security import AuthConfig

TOKEN = "test-token-" + "x" * 32
PORT = 48123
BASE_URL = f"http://127.0.0.1:{PORT}"
ORIGIN = "tauri://localhost"


@pytest.fixture
def auth() -> AuthConfig:
    return AuthConfig.for_loopback(TOKEN, PORT, [ORIGIN])


@pytest.fixture
def client(settings: PanosSettings, auth: AuthConfig) -> Iterator[TestClient]:
    """Authenticated client; the context manager runs the app lifespan (migrations, node)."""
    app = create_app(settings, auth)
    with TestClient(app, base_url=BASE_URL, headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


@pytest.fixture
def anon_client(settings: PanosSettings, auth: AuthConfig) -> Iterator[TestClient]:
    app = create_app(settings, auth)
    with TestClient(app, base_url=BASE_URL) as c:
        yield c
