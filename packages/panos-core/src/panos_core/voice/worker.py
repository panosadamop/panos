"""Voice worker process (ADR-016): ``python -m panos_core.voice.worker <config.json>``.

Supervised by the node. Protocol: JSON lines. The node writes commands to stdin
(``listen``, ``stop_listening``, ``cancel_listening``, ``speak``, ``stop_talking``,
``configure``, ``shutdown``); the worker writes events to stdout (``ready``, ``state``,
``wake``, ``transcript``, ``no_speech``, ``spoken``, ``speech_stopped``, ``error``).
No network access is needed and no audio is ever written to disk.
"""

import json
import sys
import threading
from pathlib import Path
from typing import Any

from panos_core.voice.engine import EngineSettings, VoiceEngine
from panos_core.voice.models import WHISPER_DIR, wake_word_available

_OUT_LOCK = threading.Lock()


def emit(event: dict[str, Any]) -> None:
    line = json.dumps(event, ensure_ascii=False)
    with _OUT_LOCK:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def build_engine(config: dict[str, Any]) -> VoiceEngine:
    from panos_core.voice.adapters import (
        OnnxWakeWord,
        PiperTts,
        SileroVad,
        SoundDeviceIO,
        WhisperStt,
    )

    models = Path(config["model_dir"])
    size = config.get("stt_model", "small")
    accurate = config.get("accurate_model")
    stt = WhisperStt(
        models / WHISPER_DIR / size,
        accurate_model=(models / WHISPER_DIR / accurate) if accurate else None,
        threads=config.get("threads"),
    )
    tts = PiperTts(
        models / "piper" / config.get("greek_voice", "el_GR-joy-medium.onnx"),
        models / "piper" / config.get("english_voice", "en_US-lessac-medium.onnx"),
    )
    wake_model = config.get("wake_word_model", "hey_jarvis_v0.1")
    wake = (
        OnnxWakeWord(models / "wakeword", wake_model)
        if wake_word_available(models, wake_model)
        else None
    )
    settings = EngineSettings(
        **{k: v for k, v in config.get("settings", {}).items() if hasattr(EngineSettings, k)}
    )
    return VoiceEngine(
        audio=SoundDeviceIO(config.get("input_device")),
        vad=SileroVad(models / "vad" / "silero_vad.onnx"),
        stt=stt,
        tts=tts,
        wake=wake,
        sink=emit,
        settings=settings,
    )


def serve(engine: VoiceEngine, stdin: Any = None) -> None:
    """Read commands until ``shutdown`` or EOF (the node went away)."""
    engine.start()
    for raw in stdin or sys.stdin:
        try:
            msg = json.loads(raw)
        except ValueError:
            emit({"event": "error", "detail": "invalid command"})
            continue
        cmd = msg.get("cmd")
        if cmd == "shutdown":
            break
        if cmd == "listen":
            engine.listen(msg.get("trigger", "push_to_talk"))
        elif cmd == "stop_listening":
            engine.stop_listening()
        elif cmd == "cancel_listening":
            engine.cancel_listening()
        elif cmd == "speak":
            engine.speak(str(msg.get("text", "")))
        elif cmd == "stop_talking":
            engine.stop_talking()
        elif cmd == "configure":
            engine.configure(**msg.get("settings", {}))
        else:
            emit({"event": "error", "detail": f"unknown command {cmd!r}"})
    engine.close()


def main() -> None:
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        engine = build_engine(config)
    except Exception as exc:
        emit({"event": "error", "fatal": True, "detail": f"{type(exc).__name__}: {exc}"})
        raise SystemExit(2) from exc
    serve(engine)


if __name__ == "__main__":
    main()
