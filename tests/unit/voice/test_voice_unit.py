"""Voice: text routing, engine state machine, worker protocol (fakes; no audio libs needed)."""

import builtins
import io
import json
import threading
import time
import wave
from typing import Any

import numpy as np
import pytest

from panos_core.voice.engine import EngineSettings, VoiceEngine
from panos_core.voice.ports import FRAME, Transcript
from panos_core.voice.text import normalize, route, sentences, split_language_runs
from panos_core.voice.worker import serve

pytestmark = pytest.mark.req("BR-005", "FR-001")

SPEECH = np.full(FRAME, 1000, np.int16)
SILENCE = np.zeros(FRAME, np.int16)


# ---------------------------------------------------------------------- text
@pytest.mark.req("US-003", "AC-US003-04")
def test_language_runs_send_latin_words_to_the_english_voice() -> None:
    runs = split_language_runs("Πάνος, άνοιξε το project στο IntelliJ και τρέξε τα unit tests.")
    assert runs == [
        ("el", "Πάνος, άνοιξε το"),
        ("en", "project"),
        ("el", "στο"),
        ("en", "IntelliJ"),
        ("el", "και τρέξε τα"),
        ("en", "unit tests."),
    ]
    assert split_language_runs("3 files, όλα καλά") == [("en", "3 files,"), ("el", "όλα καλά")]
    assert split_language_runs("42") == [("el", "42")]
    assert sentences("Έτοιμο. Next step? Ναι!") == ["Έτοιμο.", "Next step?", "Ναι!"]


@pytest.mark.req("US-004", "AC-US004-03")
@pytest.mark.parametrize(
    ("text", "command", "rest"),
    [
        ("Πάνο, σταμάτα να μιλάς!", "stop_talking", None),
        ("stop talking", "stop_talking", None),
        ("Panos stop", "stop", None),
        ("Σταμάτα.", "stop", None),
        ("hey panos pause", "pause", None),
        ("Πάνε, συνέχισε", "resume", None),
        ("never mind", "cancel_listening", None),
        ("Panos, open IntelliJ", "utterance", "open IntelliJ"),
        ("Πάνο, άνοιξε το project orders", "utterance", "άνοιξε το project orders"),
        ("stop the server and redeploy", "utterance", "stop the server and redeploy"),
    ],
)
def test_command_routing(text: str, command: str, rest: str | None) -> None:
    routed = route(text)
    assert routed.command == command
    if rest is not None:
        assert routed.text == rest
    assert normalize("Πάνος!") == "πανοσ"


# ---------------------------------------------------------------------- engine
class FakeAudio:
    def __init__(self) -> None:
        self.played: list[int] = []
        self.started = False
        self.stopped_playback = 0
        self.play_gate = threading.Event()
        self.play_gate.set()

    def start(self, on_frame: Any) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def play(self, audio: Any, rate: int) -> None:
        self.play_gate.wait(2)
        self.played.append(len(audio))

    def stop_playback(self) -> None:
        self.stopped_playback += 1
        self.play_gate.set()


class FakeVad:
    def is_speech(self, frame: Any) -> float:
        return 1.0 if int(np.abs(frame).max()) > 0 else 0.0

    def reset(self) -> None:
        pass


class FakeWake:
    name = "fake_wake"

    def __init__(self) -> None:
        self.next = 0.0

    def score(self, frame: Any) -> float:
        return self.next

    def reset(self) -> None:
        self.next = 0.0


class FakeStt:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def transcribe(
        self, audio: Any, *, language: str | None, prompt: str = "", accurate: bool = False
    ) -> Transcript:
        self.calls.append(len(audio))
        return Transcript(
            f"heard {len(audio)} samples", language or "en", 0.99, len(audio) / 16000, 0.01
        )


class FakeTts:
    def synthesize(self, text: str) -> Any:
        for word in text.split():
            yield np.zeros(160, np.int16), 22050
            del word


def engine(
    **settings: Any,
) -> tuple[VoiceEngine, list[dict[str, Any]], FakeAudio, FakeStt, FakeWake]:
    events: list[dict[str, Any]] = []
    audio, stt, wake = FakeAudio(), FakeStt(), FakeWake()
    eng = VoiceEngine(
        audio=audio,
        vad=FakeVad(),
        stt=stt,
        tts=FakeTts(),
        wake=wake,
        sink=events.append,
        settings=EngineSettings(**settings),
    )
    return eng, events, audio, stt, wake


def state_of(eng: VoiceEngine) -> str:
    return eng.state


def kinds(events: list[dict[str, Any]]) -> list[str]:
    return [e["event"] for e in events if e["event"] != "state"]


@pytest.mark.req("US-002")
def test_push_to_talk_captures_until_silence_then_transcribes() -> None:
    eng, events, _, stt, _ = engine(silence_s=0.4)
    eng.listen()
    assert state_of(eng) == "listening"
    for frame in [SPEECH] * 10 + [SILENCE] * 6:
        eng.process(frame)
    [transcript] = [e for e in events if e["event"] == "transcript"]
    assert transcript["text"] == f"heard {15 * FRAME} samples"  # 10 speech + 5 silence frames
    assert transcript["trigger"] == "push_to_talk" and state_of(eng) == "idle"
    assert stt.calls == [15 * FRAME]


def test_endpointing_edge_cases() -> None:
    eng, events, _, stt, _ = engine(no_speech_s=0.4, max_utterance_s=1.0, min_speech_s=0.3)
    eng.listen()
    for _ in range(6):
        eng.process(SILENCE)
    assert kinds(events)[-1] == "no_speech" and stt.calls == []
    eng.listen()
    for _ in range(20):  # never stops talking: cut at max_utterance_s
        eng.process(SPEECH)
    assert stt.calls == [13 * FRAME]  # 1.0 s / 80 ms → 13 frames
    eng.listen()
    eng.process(SPEECH)  # a click (80 ms) is not speech
    for _ in range(12):
        eng.process(SILENCE)
    assert kinds(events)[-1] == "no_speech" and len(stt.calls) == 1
    eng.listen()
    eng.process(SPEECH)
    eng.stop_listening()  # push-to-talk released before real speech
    assert kinds(events)[-1] == "no_speech"


@pytest.mark.req("US-001", "AC-US001-03", "AC-US001-04")
def test_wake_word_triggers_capture_with_grace_and_respects_mute() -> None:
    eng, events, _, stt, wake = engine(wake_word=True, threshold=0.5, silence_s=0.24)
    eng.process(SPEECH)
    assert kinds(events) == []
    wake.next = 0.9
    eng.process(SPEECH)
    assert kinds(events) == ["wake"] and state_of(eng) == "listening"
    for frame in [SPEECH] * 4 + [SPEECH] * 5 + [SILENCE] * 3:  # first 4 frames = grace
        eng.process(frame)
    assert stt.calls == [8 * FRAME]  # grace frames (0.3 s ≈ 4 frames) are not transcribed
    eng.configure(muted=True)
    assert state_of(eng) == "muted"
    wake.next = 0.99
    eng.process(SPEECH)
    assert kinds(events).count("wake") == 1  # muted: wake word ignored
    eng.configure(muted=False, wake_word=False)
    eng.process(SPEECH)
    assert kinds(events).count("wake") == 1  # disabled


@pytest.mark.req("US-004", "AC-US004-01")
def test_speech_output_and_barge_in() -> None:
    eng, events, audio, _, _ = engine()
    eng.start()
    try:
        eng.speak("one two three")
        deadline = time.monotonic() + 5
        while "spoken" not in kinds(events):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert len(audio.played) == 3
        audio.play_gate.clear()  # next playback blocks until interrupted
        eng.speak("a long reply that keeps going")
        time.sleep(0.1)
        started = time.monotonic()
        eng.listen()  # push-to-talk while speaking → speech stops (barge-in)
        assert "speech_stopped" in kinds(events) and audio.stopped_playback >= 1
        assert time.monotonic() - started < 0.5  # AC-US004-01: within 500 ms
        eng.cancel_listening()
        eng.configure(tts=False)
        eng.speak("not spoken")
        time.sleep(0.1)
        assert kinds(events).count("spoken") == 1
    finally:
        eng.close()


def test_adapter_errors_do_not_kill_the_engine() -> None:
    eng, events, _, stt, _ = engine(silence_s=0.16)

    def boom(*a: Any, **k: Any) -> Transcript:
        raise RuntimeError("model crashed")

    stt.transcribe = boom  # type: ignore[method-assign]
    eng.start()
    try:
        eng.listen()
        for frame in [SPEECH] * 5 + [SILENCE] * 3:
            eng.feed(frame)
        deadline = time.monotonic() + 5
        while "error" not in kinds(events):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert state_of(eng) == "idle"
    finally:
        eng.close()


@pytest.mark.req("AC-US001-05", "NFR-PRV-001")
def test_no_audio_is_ever_written_to_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*a: Any, **k: Any) -> Any:
        raise AssertionError(f"audio must stay in memory (attempted write: {a[:1]})")

    real_open = builtins.open

    def guarded_open(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
        if any(m in mode for m in "wax+"):
            forbidden(file)
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(wave, "open", forbidden)
    monkeypatch.setattr(io, "open", guarded_open)
    eng, events, _, stt, wake = engine(wake_word=True, silence_s=0.16)
    wake.next = 0.9
    eng.process(SPEECH)
    for frame in [SPEECH] * 10 + [SILENCE] * 3:
        eng.process(frame)
    assert stt.calls and kinds(events)[-1] == "transcript"


# ---------------------------------------------------------------------- worker protocol
def test_worker_serves_json_line_commands() -> None:
    calls: list[tuple[str, Any]] = []

    class Spy:
        def start(self) -> None:
            calls.append(("start", None))

        def close(self) -> None:
            calls.append(("close", None))

        def listen(self, trigger: str) -> None:
            calls.append(("listen", trigger))

        def stop_listening(self) -> None:
            calls.append(("stop_listening", None))

        def cancel_listening(self) -> None:
            calls.append(("cancel", None))

        def speak(self, text: str) -> None:
            calls.append(("speak", text))

        def stop_talking(self) -> None:
            calls.append(("stop_talking", None))

        def configure(self, **kw: Any) -> None:
            calls.append(("configure", kw))

    lines = [
        json.dumps(m) + "\n"
        for m in (
            {"cmd": "listen"},
            {"cmd": "stop_listening"},
            {"cmd": "speak", "text": "γεια"},
            {"cmd": "stop_talking"},
            {"cmd": "configure", "settings": {"muted": True}},
            {"cmd": "cancel_listening"},
            {"cmd": "shutdown"},
            {"cmd": "listen"},
        )
    ]
    serve(Spy(), stdin=iter(lines))  # type: ignore[arg-type]
    assert calls == [
        ("start", None),
        ("listen", "push_to_talk"),
        ("stop_listening", None),
        ("speak", "γεια"),
        ("stop_talking", None),
        ("configure", {"muted": True}),
        ("cancel", None),
        ("close", None),
    ]
