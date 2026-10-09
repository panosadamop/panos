"""Real voice models (gated): set ``PANOS_VOICE_MODELS`` to a provisioned model directory
(``panos voice setup``). Test audio is synthesized with Piper at run time, so no recordings
are committed. Skipped when the models are not available (e.g. CI)."""

import os
import socket
import time
from pathlib import Path
from typing import Any

import pytest

from panos_core.voice.models import missing, wake_word_available

MODELS = Path(os.environ.get("PANOS_VOICE_MODELS", "__none__"))
pytestmark = [
    pytest.mark.req("BR-005", "FR-001"),
    pytest.mark.skipif(
        not MODELS.is_dir() or bool(missing(MODELS, "small")) or not wake_word_available(MODELS),
        reason="voice models not provisioned (set PANOS_VOICE_MODELS)",
    ),
    pytest.mark.timeout(300),
]


@pytest.fixture(scope="module")
def tts() -> Any:
    from panos_core.voice.adapters import PiperTts

    return PiperTts(
        MODELS / "piper/el_GR-joy-medium.onnx", MODELS / "piper/en_US-lessac-medium.onnx"
    )


def render(tts: Any, text: str) -> Any:
    import numpy as np

    parts = []
    for audio, rate in tts.synthesize(text):
        idx = np.linspace(0, len(audio) - 1, int(len(audio) * 16000 / rate))
        parts.append(np.interp(idx, np.arange(len(audio)), audio).astype(np.int16))
    return np.concatenate(parts)


def frames(audio: Any) -> Any:
    import numpy as np

    padded = np.concatenate([np.zeros(16000, np.int16), audio, np.zeros(16000, np.int16)])
    for i in range(0, len(padded) - 1280, 1280):
        yield padded[i : i + 1280]


@pytest.mark.req("US-001", "AC-US001-01", "AC-US001-02")
def test_wake_word_works_with_networking_blocked(tts: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from panos_core.voice.adapters import OnnxWakeWord, SileroVad

    phrases = {
        text: render(tts, text)
        for text in ("Hey Jarvis", "Good morning everyone", "Open the project in IntelliJ")
    }

    def no_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the wake word must not use the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    detector = OnnxWakeWord(MODELS / "wakeword")
    vad = SileroVad(MODELS / "vad/silero_vad.onnx")
    scores = {}
    for text, audio in phrases.items():
        detector.reset()
        scores[text] = max(detector.score(f) for f in frames(audio))
    assert scores["Hey Jarvis"] > 0.9
    assert scores["Good morning everyone"] < 0.1 and scores["Open the project in IntelliJ"] < 0.1
    speech = sum(vad.is_speech(f) > 0.5 for f in frames(phrases["Good morning everyone"]))
    assert 5 <= speech <= 30  # ~1 s of speech inside 3 s of audio


@pytest.mark.req("US-001", "AC-US001-03", "US-002")
def test_full_pipeline_wake_word_to_transcript_to_speech(tts: Any) -> None:
    from panos_core.voice.adapters import OnnxWakeWord, SileroVad, WhisperStt
    from panos_core.voice.engine import EngineSettings, VoiceEngine

    class MemoryAudio:
        def __init__(self) -> None:
            self.played: list[int] = []

        def start(self, on_frame: Any) -> None: ...
        def stop(self) -> None: ...

        def play(self, audio: Any, rate: int) -> None:
            self.played.append(len(audio))

        def stop_playback(self) -> None: ...

    import numpy as np

    events: list[dict[str, Any]] = []
    audio = MemoryAudio()
    engine = VoiceEngine(
        audio=audio,
        vad=SileroVad(MODELS / "vad/silero_vad.onnx"),
        stt=WhisperStt(MODELS / "whisper/small"),
        tts=tts,
        wake=OnnxWakeWord(MODELS / "wakeword"),
        sink=events.append,
        settings=EngineSettings(wake_word=True, language=None, prompt="Panos, IntelliJ, orders"),
    )
    stream = np.concatenate(
        [
            render(tts, "Hey Jarvis"),
            np.zeros(6000, np.int16),
            render(tts, "Open the orders project in IntelliJ."),
        ]
    )
    for frame in frames(stream):
        engine.process(frame)
    names = [e["event"] for e in events if e["event"] != "state"]
    assert names[:2] == ["wake", "transcript"], events
    transcript = next(e for e in events if e["event"] == "transcript")
    assert "intellij" in transcript["text"].lower() and "orders" in transcript["text"].lower()
    assert transcript["language"] == "en" and transcript["trigger"] == "wake_word"

    engine.start()
    try:
        engine.speak("Άνοιξα το project orders στο IntelliJ.")
        deadline = time.monotonic() + 60
        while not any(e["event"] == "spoken" for e in events):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert len(audio.played) >= 4  # Greek and English runs: separate voices
    finally:
        engine.close()
