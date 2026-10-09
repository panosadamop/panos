"""Screenshots: explicit capture, in memory, local vision by default (ADR-025, US-013)."""

import builtins
import json
from pathlib import Path
from typing import Any

import pytest

from panos_core.ai.router import NoRouteError
from panos_core.domain.enums import PrivacyMode
from panos_core.platform.desktop import WindowInfo
from panos_core.screen import ScreenError, Screenshot
from panos_node_api.container import NodeContainer
from tests.ai_fakes import CLOUD_CHAT, LOCAL_VISION, MockAI

pytestmark = pytest.mark.req("FR-015", "BR-021", "US-013")

PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"
WINDOW = WindowInfo("IntelliJ IDEA - orders", 4242, "idea64.exe", None, (0, 0, 800, 600))


@pytest.fixture
def captured(node: NodeContainer) -> list[int]:
    calls: list[int] = []

    def fake_capture(desktop: Any) -> Screenshot:
        calls.append(1)
        return Screenshot(png=PNG, width=800, height=600, window=WINDOW)

    node.screen._capture = fake_capture
    return calls


async def standard_project(node: NodeContainer, tmp_path: Path, name: str = "web") -> Any:
    (tmp_path / name).mkdir()
    project = await node.projects.register(tmp_path / name)
    return await node.projects.set_privacy_mode(project.id, PrivacyMode.STANDARD, actor="user")


@pytest.mark.req("AC-US013-01", "AC-US013-02", "AC-US013-03", "NFR-PRV-002")
async def test_screenshot_is_local_only_in_memory_and_audited_without_image(
    node: NodeContainer, mock_ai: MockAI, captured: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open = builtins.open

    def no_writes(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
        if any(m in mode for m in "wax+"):
            raise AssertionError(f"screenshots must never be written ({file})")
        return real_open(file, mode, *a, **k)

    mock_ai.local.responder = lambda r, m: "A NullPointerException in OrderService line 42."
    monkeypatch.setattr(builtins, "open", no_writes)
    result = await node.screen.analyze("what is the error?", allow_cloud=True)  # no project
    monkeypatch.undo()
    assert captured == [1]
    assert result.privacy == PrivacyMode.LOCAL_ONLY and result.locality == "local"
    assert result.model == LOCAL_VISION and "NullPointerException" in result.text
    sent, _ = mock_ai.local.calls[-1]
    assert sent.messages[1].images and sent.privacy == PrivacyMode.LOCAL_ONLY
    assert mock_ai.cloud.calls == []  # consent alone is not enough without a project policy
    [event] = await node.audit.list(event_type="screen.captured")
    assert event.metadata["persisted"] is False and event.metadata["privacy"] == "LOCAL_ONLY"
    assert "png" not in json.dumps(event.metadata).lower() and "fake-image" not in json.dumps(
        event.metadata
    )


@pytest.mark.req("AC-US013-03")
async def test_cloud_vision_needs_consent_and_a_standard_project(
    node: NodeContainer, mock_ai: MockAI, captured: list[int], tmp_path: Path
) -> None:
    project = await standard_project(node, tmp_path)
    local = await node.screen.analyze("", project=project)  # no consent → local
    assert local.locality == "local" and mock_ai.cloud.calls == []
    cloud = await node.screen.analyze("", project=project, allow_cloud=True)
    assert cloud.privacy == PrivacyMode.STANDARD and cloud.model == CLOUD_CHAT
    assert mock_ai.cloud.calls[-1][0].messages[1].images  # redaction keeps the image


async def test_without_a_local_vision_model_local_screens_are_refused(
    node: NodeContainer, mock_ai: MockAI, captured: list[int]
) -> None:
    mock_ai.local.models = tuple(m for m in mock_ai.local.models if m != LOCAL_VISION)
    await node.gateway.refresh_health(force=True)
    with pytest.raises(NoRouteError) as err:
        await node.screen.analyze("")
    assert "local-only" in err.value.summary and mock_ai.cloud.calls == []


@pytest.mark.req("AC-US013-04", "FR-002")
async def test_asking_in_chat_captures_and_explains(
    node: NodeContainer, mock_ai: MockAI, captured: list[int]
) -> None:
    mock_ai.local.responder = lambda r, m: "The build failed: missing semicolon."
    reply = await node.conversations.send("Τι βλέπεις στην οθόνη;")
    assert reply.intent == "screen" and "missing semicolon" in reply.text and captured == [1]
    plain = await node.conversations.send("what is a screen reader?")
    assert plain.intent == "chat" and captured == [1]  # never captured without asking


async def test_capture_failures_are_reported(node: NodeContainer) -> None:
    def broken(desktop: Any) -> Screenshot:
        raise RuntimeError("no display")

    node.screen._capture = broken
    with pytest.raises(ScreenError, match="no display"):
        await node.screen.analyze("")
    reply = await node.conversations.send("look at my screen")
    assert reply.text.startswith("I could not analyze the screen")
