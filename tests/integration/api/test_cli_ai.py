"""``panos ask`` / ``panos ai`` against the in-process node (TestClient as the HTTP client)."""

import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from panos_cli import common
from panos_cli.client import NodeClient
from panos_cli.main import app
from panos_core.config.runtime import RuntimeInfo
from panos_core.domain.clock import utc_now
from tests.ai_fakes import MockAI
from tests.integration.api.conftest import BASE_URL, TOKEN

pytestmark = pytest.mark.req("FR-002", "FR-021")

runner = CliRunner()


class _Bridge(httpx.BaseTransport):
    """Real ``httpx`` client → in-process app (Starlette's TestClient uses another httpx)."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        r = self._client.request(
            request.method, str(request.url), content=request.read(), headers=dict(request.headers)
        )
        return httpx.Response(r.status_code, headers=list(r.headers.items()), content=r.content)


@pytest.fixture
def cli(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the CLI's NodeClient at the in-process app."""
    info = RuntimeInfo(
        host="127.0.0.1", port=48123, token=TOKEN, version="test", pid=0, started_at=utc_now()
    )

    def from_settings(cls: type[NodeClient], settings: object) -> NodeClient:
        node = NodeClient.__new__(NodeClient)
        node.info = info
        # Each CLI command closes its client; the app (and its lifespan) stays up.
        node._client = httpx.Client(
            base_url=f"{BASE_URL}/api/v1",
            transport=_Bridge(client),
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        return node

    monkeypatch.setattr(NodeClient, "from_settings", classmethod(from_settings))
    yield
    monkeypatch.undo()


@pytest.mark.usefixtures("cli")
def test_ask_streams_answer_and_reports_routing(mock_ai: MockAI) -> None:
    mock_ai.respond(lambda r, m: "Γεια σου, είμαι ο Panos")
    result = runner.invoke(app, ["ask", "-v", "γεια"])
    assert result.exit_code == 0, result.output
    assert "Γεια σου, είμαι ο Panos" in result.stdout
    assert "local/mock-local-chat" in result.output  # routing line (stderr) with -v
    as_json = runner.invoke(app, ["ask", "--json", "list projects"])
    assert as_json.exit_code == 0, as_json.output
    # In-process, the node's own log lines share stdout: parse from the JSON document.
    data = json.loads(as_json.stdout[as_json.stdout.index("{\n") :])
    assert data["intent"] == "list_projects" and data["provider"] is None


@pytest.mark.usefixtures("cli")
def test_ask_without_local_model_explains(mock_ai: MockAI) -> None:
    mock_ai.local.available = False
    result = runner.invoke(app, ["ask", "-v", "summarize"])
    assert result.exit_code == common.EXIT_ERROR
    assert "local-only" in result.output and "LOCAL_ONLY" in result.output
    missing = runner.invoke(app, ["ask", "-c", "cnv_nope", "hi"])
    assert missing.exit_code == common.EXIT_ERROR and "404" in missing.output


@pytest.mark.usefixtures("cli")
def test_ai_commands() -> None:
    providers = runner.invoke(app, ["ai", "providers"])
    assert providers.exit_code == 0 and "local" in providers.stdout and "cloud" in providers.stdout
    listed = runner.invoke(app, ["ai", "providers", "--json"]).stdout
    assert json.loads(listed[listed.index("[\n") :])[0]["ok"]
    models = runner.invoke(app, ["ai", "models"])
    assert "mock-local-chat" in models.stdout and "free" in models.stdout
    route = runner.invoke(app, ["ai", "route", "x", "--privacy", "standard"])
    assert route.exit_code == 0 and route.stdout.startswith("cloud/mock-cloud-chat")
    usage = runner.invoke(app, ["ai", "usage"])
    assert usage.exit_code == 0 and "monthly budget" in usage.stdout
    assert "providers available" in runner.invoke(app, ["ai", "reload"]).stdout
    unknown = runner.invoke(app, ["ai", "set-key", "nope", "--stdin"], input="k" * 20 + "\n")
    assert unknown.exit_code == common.EXIT_ERROR
