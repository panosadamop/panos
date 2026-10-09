"""Voice engine: trigger → capture → STT → events; and speech output with barge-in.

Runs inside the voice worker process (ADR-016). Ports are injected, so the engine is tested
with fakes. Audio stays in memory: captured frames live in a list that is dropped after
transcription (AC-US001-05, NFR-PRV-001).

Triggers: push-to-talk (``listen``) is primary; the wake word is optional and can be muted.
Endpointing: the utterance ends after ``silence_s`` of non-speech following speech, at
``max_utterance_s``, or is abandoned if nobody speaks within ``no_speech_s``.
"""

import contextlib
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from panos_core.voice.ports import (
    FRAME,
    SAMPLE_RATE,
    Audio,
    AudioIO,
    Sink,
    SpeechToText,
    TextToSpeech,
    VoiceActivityDetector,
    WakeWordDetector,
)

State = Literal["idle", "listening", "transcribing", "muted"]
FRAME_S = FRAME / SAMPLE_RATE


@dataclass
class EngineSettings:
    wake_word: bool = False
    threshold: float = 0.5
    muted: bool = False
    language: str | None = "el"
    prompt: str = ""  # vocabulary prompt (project, tool and IDE names)
    speech_threshold: float = 0.5
    silence_s: float = 0.8
    no_speech_s: float = 5.0
    max_utterance_s: float = 15.0
    min_speech_s: float = 0.3  # speech needed before silence can end the utterance
    wake_grace_s: float = 0.3  # ignore the tail of the wake phrase
    tts: bool = True


@dataclass
class _Capture:
    trigger: str
    frames: list[Audio] = field(default_factory=list)
    heard_speech: bool = False
    speech_s: float = 0.0
    skip_s: float = 0.0
    silence_s: float = 0.0
    elapsed_s: float = 0.0


class VoiceEngine:
    def __init__(
        self,
        *,
        audio: AudioIO,
        vad: VoiceActivityDetector,
        stt: SpeechToText,
        tts: TextToSpeech | None,
        sink: Sink,
        wake: WakeWordDetector | None = None,
        settings: EngineSettings | None = None,
    ) -> None:
        self._audio, self._vad, self._stt, self._tts, self._wake = audio, vad, stt, tts, wake
        self._sink = sink
        self.settings = settings or EngineSettings()
        self.state: State = "muted" if self.settings.muted else "idle"
        self._frames: queue.Queue[Audio] = queue.Queue(maxsize=500)
        self._capture: _Capture | None = None
        self._speaking = threading.Event()
        self._speech_queue: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._threads = [
            threading.Thread(target=self._process_loop, daemon=True),
            threading.Thread(target=self._speak_loop, daemon=True),
        ]
        for t in self._threads:
            t.start()
        self._audio.start(self.feed)
        self._emit(
            "ready", wake_word=self._wake.name if self._wake else None, tts=self._tts is not None
        )
        self._set_state(self.state)

    def close(self) -> None:
        self._stop.set()
        self._audio.stop()
        self._speech_queue.put(None)
        for t in self._threads:
            t.join(timeout=5)

    # ------------------------------------------------------------------ commands
    def feed(self, frame: Audio) -> None:
        """Called by the capture thread for every FRAME. Never blocks."""
        with contextlib.suppress(queue.Full):  # drop rather than block the audio callback
            self._frames.put_nowait(frame)

    def listen(self, trigger: str = "push_to_talk") -> None:
        if self._speaking.is_set():
            self.stop_talking()  # barge-in
        self._vad.reset()
        self._capture = _Capture(
            trigger=trigger,
            skip_s=self.settings.wake_grace_s if trigger == "wake_word" else 0.0,
        )
        self._set_state("listening")

    def stop_listening(self) -> None:
        """Push-to-talk released: finish the utterance now."""
        if self._capture is not None:
            self._finish(self._capture)

    def cancel_listening(self) -> None:
        self._capture = None
        self._set_state("muted" if self.settings.muted else "idle")

    def speak(self, text: str) -> None:
        if self._tts is not None and self.settings.tts and text.strip():
            self._speech_queue.put(text)

    def stop_talking(self) -> None:
        while not self._speech_queue.empty():
            try:
                self._speech_queue.get_nowait()
            except queue.Empty:
                break
        self._speaking.clear()
        self._audio.stop_playback()
        self._emit("speech_stopped")

    def configure(self, **changes: Any) -> None:
        for key, value in changes.items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)
        if self._capture is None:
            self._set_state("muted" if self.settings.muted else "idle")

    # ------------------------------------------------------------------ processing
    def _process_loop(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self._frames.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.process(frame)
            except Exception as exc:  # keep listening after adapter errors
                self._emit("error", detail=f"{type(exc).__name__}: {exc}")
                self._capture = None
                self._set_state("muted" if self.settings.muted else "idle")

    def process(self, frame: Audio) -> None:
        """One FRAME of microphone audio (public for deterministic tests)."""
        capture = self._capture
        if capture is None:
            if self._wake is not None and self.settings.wake_word and not self.settings.muted:
                score = self._wake.score(frame)
                if score >= self.settings.threshold:
                    self._wake.reset()
                    self._emit("wake", score=round(score, 3), detector=self._wake.name)
                    self.listen(trigger="wake_word")
            return
        if capture.skip_s > 0:
            capture.skip_s -= FRAME_S
            return
        capture.frames.append(frame)
        capture.elapsed_s += FRAME_S
        if self._vad.is_speech(frame) >= self.settings.speech_threshold:
            capture.speech_s += FRAME_S
            capture.silence_s = 0.0
            capture.heard_speech = capture.speech_s >= self.settings.min_speech_s
        else:
            capture.silence_s += FRAME_S
        s = self.settings
        ended = capture.heard_speech and capture.silence_s >= s.silence_s
        if ended or capture.elapsed_s >= s.max_utterance_s:
            self._finish(capture)
        elif not capture.heard_speech and capture.elapsed_s >= s.no_speech_s:
            self._capture = None
            self._emit("no_speech", trigger=capture.trigger)
            self._set_state("muted" if s.muted else "idle")

    def _finish(self, capture: _Capture) -> None:
        self._capture = None
        if not capture.heard_speech:
            self._emit("no_speech", trigger=capture.trigger)
            self._set_state("muted" if self.settings.muted else "idle")
            return
        self._set_state("transcribing")
        import numpy as np

        audio = np.concatenate(capture.frames)
        capture.frames.clear()  # in memory only; dropped right after transcription
        try:
            result = self._stt.transcribe(
                audio, language=self.settings.language, prompt=self.settings.prompt
            )
        finally:
            del audio
            self._drain()
            self._set_state("muted" if self.settings.muted else "idle")
        self._emit(
            "transcript",
            text=result.text,
            language=result.language,
            language_probability=round(result.language_probability, 3),
            duration_s=round(result.duration_s, 2),
            stt_s=round(result.stt_s, 2),
            trigger=capture.trigger,
        )

    def _drain(self) -> None:
        """Frames that queued up while transcribing are stale."""
        while not self._frames.empty():
            try:
                self._frames.get_nowait()
            except queue.Empty:
                break

    def _speak_loop(self) -> None:
        while not self._stop.is_set():
            text = self._speech_queue.get()
            if text is None:
                return
            if self._tts is None:
                continue
            self._speaking.set()
            started = time.monotonic()
            try:
                for audio, rate in self._tts.synthesize(text):
                    if not self._speaking.is_set():
                        break
                    self._audio.play(audio, rate)
            except Exception as exc:
                self._emit("error", detail=f"tts: {type(exc).__name__}: {exc}")
            finally:
                completed = self._speaking.is_set()
                self._speaking.clear()
            if completed:
                self._emit("spoken", seconds=round(time.monotonic() - started, 2))

    # ------------------------------------------------------------------ events
    def _set_state(self, state: State) -> None:
        self.state = state
        self._emit("state", state=state)

    def _emit(self, event: str, **data: Any) -> None:
        self._sink({"event": event, **data})
