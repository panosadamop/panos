"""Voice ports (ADR-016). Audio is 16 kHz mono int16 numpy arrays, kept in memory only."""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

SAMPLE_RATE = 16_000
FRAME = 1280  # 80 ms: the wake-word frame size; capture runs at this granularity
Audio = Any  # numpy.ndarray[int16]; numpy is an optional dependency (voice extra)


class AudioIO(Protocol):
    """Microphone input and speaker output."""

    def start(self, on_frame: Callable[[Audio], None]) -> None:
        """Start delivering FRAME-sized int16 chunks to ``on_frame`` (from a capture thread)."""

    def stop(self) -> None: ...

    def play(self, audio: Audio, sample_rate: int) -> None:
        """Play int16 audio; blocks until done or ``stop_playback``."""

    def stop_playback(self) -> None: ...


class WakeWordDetector(Protocol):
    name: str

    def score(self, frame: Audio) -> float:
        """Score one FRAME (0..1)."""

    def reset(self) -> None: ...


class VoiceActivityDetector(Protocol):
    def is_speech(self, frame: Audio) -> float:
        """Speech probability for one FRAME (0..1)."""

    def reset(self) -> None: ...


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str
    language_probability: float
    duration_s: float
    stt_s: float


class SpeechToText(Protocol):
    def transcribe(
        self, audio: Audio, *, language: str | None, prompt: str = "", accurate: bool = False
    ) -> Transcript: ...


class TextToSpeech(Protocol):
    def synthesize(self, text: str) -> Iterator[tuple[Audio, int]]:
        """Yield (int16 audio, sample_rate) per sentence/language run (streaming)."""


Sink = Callable[[dict[str, Any]], None]  # worker → node event sink
Clock = Callable[[], float]
Prompts = Sequence[str]
