"""Pure text helpers for voice: language-run splitting (TTS) and command routing (VOICE.md).

Both are dependency-free so they are testable without audio libraries.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

_GREEK: Final = re.compile(r"[Ͱ-Ͽἀ-῿]")
_LATIN: Final = re.compile(r"[A-Za-z]")
_SENTENCE: Final = re.compile(r"(?<=[.!?;·])\s+|\n+")


def script_of(token: str) -> Literal["el", "en", "neutral"]:
    if _GREEK.search(token):
        return "el"
    if _LATIN.search(token):
        return "en"
    return "neutral"


def split_language_runs(
    text: str, default: Literal["el", "en"] = "el"
) -> list[tuple[Literal["el", "en"], str]]:
    """Split mixed Greek/English text into runs per script (ADR-016), so Latin-script words
    ("IntelliJ", "unit tests") go to the English voice and Greek to the Greek voice.
    Neutral tokens (numbers, punctuation) join the run they are in."""
    runs: list[tuple[Literal["el", "en"], str]] = []
    current: Literal["el", "en"] | None = None
    buf: list[str] = []
    for token in re.findall(r"\S+\s*", text):
        kind = script_of(token)
        if kind == "neutral" or kind == current:
            buf.append(token)
            continue
        if current is not None and buf:
            runs.append((current, "".join(buf).strip()))
            buf = []
        elif buf:  # leading neutral tokens join the first real run
            pass
        current = kind
        buf.append(token)
    if buf:
        runs.append((current or default, "".join(buf).strip()))
    # Merge tiny neutral-only remnants and empty runs.
    return [(lang, chunk) for lang, chunk in runs if chunk]


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text) if s and s.strip()]


def normalize(text: str) -> str:
    """Lowercase, strip accents/punctuation: for fuzzy command matching on STT output."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w\s]", " ", stripped.replace("ς", "σ")).split())


VoiceCommand = Literal[
    "stop_talking", "stop", "pause", "resume", "mute", "cancel_listening", "utterance"
]


@dataclass(frozen=True)
class Routed:
    command: VoiceCommand
    text: str  # cleaned utterance (wake word removed)


_WAKE_PREFIX: Final = re.compile(r"^(?:hey\s+)?(?:panos|πανο|πανε|πανοσ|jarvis)\b[\s,]*")
# Exact short commands only: anything longer is a normal request for the conversation.
_COMMANDS: Final[dict[str, VoiceCommand]] = {
    "stop talking": "stop_talking",
    "be quiet": "stop_talking",
    "silence": "stop_talking",
    "σταματα να μιλασ": "stop_talking",
    "σωπα": "stop_talking",
    "σιωπη": "stop_talking",
    "stop": "stop",
    "σταματα": "stop",
    "ακυρο": "stop",
    "cancel": "stop",
    "pause": "pause",
    "παυση": "pause",
    "resume": "resume",
    "continue": "resume",
    "συνεχισε": "resume",
    "mute": "mute",
    "μην ακουσ": "mute",
    "never mind": "cancel_listening",
    "τιποτα": "cancel_listening",
}


def route(text: str) -> Routed:
    """VOICE.md: ``stop talking`` cancels speech only; ``stop``/``pause``/``resume`` control
    the active job; everything else goes to the conversation."""
    cleaned = _WAKE_PREFIX.sub("", normalize(text)).strip()
    command = _COMMANDS.get(cleaned)
    if command is not None:
        return Routed(command, cleaned)
    original = re.sub(
        r"^\s*(?:hey\s+)?(?:panos|πάνο|πάνε|πάνος|jarvis)\b[\s,.:!]*", "", text, flags=re.I
    ).strip()
    return Routed("utterance", original)
