from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from panos_core.config import PanosSettings
from panos_node_api import create_app
from panos_node_api.container import AIOverride
from panos_node_api.security import AuthConfig
from tests.ai_fakes import MockAI
from tests.fakes import FakeDesktop

TOKEN = "test-token-" + "x" * 32
PORT = 48123
BASE_URL = f"http://127.0.0.1:{PORT}"
ORIGIN = "tauri://localhost"


@pytest.fixture
def desktop() -> FakeDesktop:
    """API tests never touch the real OS: discovery and launches are recorded."""
    return FakeDesktop()


@pytest.fixture
def mock_ai() -> MockAI:
    """Scripted local + cloud model providers; tests inspect what each one received."""
    return MockAI()


@pytest.fixture
def auth() -> AuthConfig:
    return AuthConfig.for_loopback(TOKEN, PORT, [ORIGIN])


@pytest.fixture
def client(
    settings: PanosSettings, auth: AuthConfig, desktop: FakeDesktop, mock_ai: MockAI
) -> Iterator[TestClient]:
    """Authenticated client; the context manager runs the app lifespan (migrations, node)."""
    ai = AIOverride(dict(mock_ai.providers), mock_ai.models)
    app = create_app(settings, auth, desktop=desktop, ai=ai)
    with TestClient(app, base_url=BASE_URL, headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


@pytest.fixture
def anon_client(
    settings: PanosSettings, auth: AuthConfig, desktop: FakeDesktop
) -> Iterator[TestClient]:
    app = create_app(settings, auth, desktop=desktop)
    with TestClient(app, base_url=BASE_URL) as c:
        yield c
