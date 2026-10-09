"""Active-window capture and analysis (ADR-025, FR-015, US-013, NFR-PRV-002).

* Capture happens only on an explicit request (API, CLI, voice or chat) or an approved
  workflow step; every capture is audited with metadata only (no image).
* The image is kept in memory, labelled LOCAL_ONLY and analyzed by a local vision model.
  A cloud model is used only with explicit per-request consent **and** when the active
  project's policy allows it (STANDARD); otherwise the label stays LOCAL_ONLY and the router
  refuses cloud models.
* Nothing is written to disk; the image is dropped after the analysis.
"""

import base64
from collections.abc import Callable
from dataclasses import dataclass

from panos_core.ai.gateway import AIGateway, CallContext
from panos_core.ai.router import RouteDecision
from panos_core.ai.types import ChatRequest, Complexity, Message, Role, TaskType
from panos_core.audit import AuditService
from panos_core.domain import Project
from panos_core.domain.enums import PrivacyMode
from panos_core.domain.errors import DomainError
from panos_core.platform.desktop import DesktopAdapter, WindowInfo

PROMPT = (
    "You are looking at a screenshot of the user's active window. Describe any visible "
    "errors, failing tests or warnings precisely (quote the message), explain the likely "
    "cause and suggest concrete next steps. Do not perform actions; suggest them. Answer in "
    "the user's language."
)


class ScreenError(DomainError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Screenshot:
    png: bytes
    width: int
    height: int
    window: WindowInfo


@dataclass(frozen=True)
class ScreenAnalysis:
    text: str
    privacy: PrivacyMode
    window_title: str
    process: str | None
    width: int
    height: int
    provider: str
    model: str
    locality: str


Capture = Callable[[DesktopAdapter], Screenshot]


def capture_active_window(desktop: DesktopAdapter) -> Screenshot:
    """Grab the foreground window's rectangle (whole primary screen if bounds are unknown)."""
    import mss
    import mss.tools

    window = desktop.active_window()
    with mss.MSS() as grabber:
        if window.bounds:
            left, top, right, bottom = window.bounds
            region = {
                "left": left,
                "top": top,
                "width": max(1, right - left),
                "height": max(1, bottom - top),
            }
        else:
            region = dict(grabber.monitors[1])
        shot = grabber.grab(region)
        png = mss.tools.to_png(shot.rgb, shot.size)  # bytes; nothing written to disk
    if not png:
        raise ScreenError("capture_failed", "the screen could not be captured")
    return Screenshot(png=png, width=shot.size.width, height=shot.size.height, window=window)


class ScreenService:
    def __init__(
        self,
        desktop: DesktopAdapter,
        gateway: AIGateway,
        audit: AuditService,
        capture: Capture = capture_active_window,
    ) -> None:
        self._desktop = desktop
        self._gateway = gateway
        self._audit = audit
        self._capture = capture

    async def analyze(
        self,
        question: str = "",
        *,
        project: Project | None = None,
        allow_cloud: bool = False,
        actor: str = "user",
    ) -> ScreenAnalysis:
        try:
            shot = self._capture(self._desktop)
        except ScreenError:
            raise
        except Exception as exc:
            raise ScreenError("capture_failed", f"screen capture failed: {exc}") from exc
        # LOCAL_ONLY unless the user consented *and* the project allows cloud use.
        cloud_ok = (
            allow_cloud and project is not None and project.privacy_mode == PrivacyMode.STANDARD
        )
        privacy = PrivacyMode.STANDARD if cloud_ok else PrivacyMode.LOCAL_ONLY
        await self._audit.record(
            actor=actor,
            event_type="screen.captured",
            action="screen.capture",
            project_id=project.id if project else None,
            metadata={
                "window": shot.window.title[:200],
                "process": shot.window.process_name,
                "width": shot.width,
                "height": shot.height,
                "privacy": privacy.value,
                "cloud_consent": allow_cloud,
                "persisted": False,
            },
        )
        image = base64.b64encode(shot.png).decode("ascii")
        request = ChatRequest(
            messages=(
                Message(role=Role.SYSTEM, content=PROMPT, privacy=PrivacyMode.STANDARD),
                Message(
                    role=Role.USER,
                    privacy=privacy,
                    images=(image,),
                    content=question or "What is wrong on this screen and what should I do next?",
                ),
            ),
            task=TaskType.VISION,
            prefer_cloud=cloud_ok,
            complexity=Complexity.MEDIUM,
            max_output_tokens=1024,
        )
        del image
        result = await self._gateway.complete(
            request, CallContext(actor=actor, project_id=project.id if project else None)
        )
        decision: RouteDecision = result.decision
        return ScreenAnalysis(
            text=result.response.text,
            privacy=privacy,
            window_title=shot.window.title,
            process=shot.window.process_name,
            width=shot.width,
            height=shot.height,
            provider=decision.provider_id,
            model=decision.model,
            locality=decision.locality.value,
        )
