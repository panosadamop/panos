"""Voice control (ADR-016, US-001..004): status, start/stop, push-to-talk, speech output."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from panos_node_api.routers.deps import Container

router = APIRouter(prefix="/voice", tags=["voice"])


class VoiceStatusView(BaseModel):
    enabled: bool
    running: bool
    state: str
    wake_word: str | None
    wake_word_enabled: bool
    tts: bool
    voice_notifications: bool
    language: str | None
    last_transcript: str | None
    last_error: str | None
    restarts: int
    missing_models: list[str]


class SpeakRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=5000)


class UtteranceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=5000)


class VoiceSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wake_word: bool | None = None
    muted: bool | None = None
    threshold: float | None = Field(default=None, gt=0, lt=1)
    tts: bool | None = None
    language: str | None = None
    voice_notifications: bool | None = None


def _status(container: Any) -> VoiceStatusView:
    v = container.voice
    s = v.status
    return VoiceStatusView(
        enabled=container.settings.voice.enabled,
        running=s.running,
        state=s.state,
        wake_word=s.wake_word,
        wake_word_enabled=v.settings.wake_word,
        tts=v.settings.tts,
        voice_notifications=v.settings.voice_notifications,
        language=v.settings.language,
        last_transcript=s.last_transcript,
        last_error=s.last_error,
        restarts=s.restarts,
        missing_models=s.missing_models,
    )


@router.get("/status", response_model=VoiceStatusView)
async def status(container: Container) -> VoiceStatusView:
    return _status(container)


@router.post("/start", response_model=VoiceStatusView)
async def start(container: Container) -> VoiceStatusView:
    await container.voice.start()
    return _status(container)


@router.post("/stop", response_model=VoiceStatusView)
async def stop(container: Container) -> VoiceStatusView:
    await container.voice.stop()
    return _status(container)


@router.post("/listen")
async def listen(container: Container) -> dict[str, bool]:
    """Push-to-talk pressed (the desktop's global hotkey calls this)."""
    return {"sent": await container.voice.listen()}


@router.post("/listen/stop")
async def listen_stop(container: Container) -> dict[str, bool]:
    """Push-to-talk released."""
    return {"sent": await container.voice.stop_listening()}


@router.post("/speak")
async def speak(body: SpeakRequest, container: Container) -> dict[str, bool]:
    return {"sent": await container.voice.speak(body.text)}


@router.post("/stop-talking")
async def stop_talking(container: Container) -> dict[str, bool]:
    return {"sent": await container.voice.stop_talking()}


@router.post("/utterance")
async def utterance(body: UtteranceRequest, container: Container) -> dict[str, str]:
    """Handle text as if it had been spoken (routing, job control, conversation)."""
    return {"result": await container.voice.handle_transcript(body.text)}


@router.patch("/settings", response_model=VoiceStatusView)
async def update_settings(body: VoiceSettingsUpdate, container: Container) -> VoiceStatusView:
    changes = body.model_dump(exclude_none=True)
    await container.voice.configure(**changes)
    return _status(container)
