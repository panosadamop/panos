"""Voice adapters (ADR-016). Heavy imports happen inside constructors so that the rest of
Panos never needs the ``voice`` extra.

* :class:`OnnxWakeWord` — openWakeWord's three-model pipeline (melspectrogram → speech
  embedding → classifier) run directly on onnxruntime. Matches the package's streaming
  behaviour without its ``tflite-runtime`` dependency (no Linux wheels for Python ≥ 3.12).
* :class:`SileroVad` — Silero VAD v5 ONNX (512-sample chunks + 64-sample context).
* :class:`WhisperStt` — faster-whisper int8 with a vocabulary prompt, no timestamps, no
  conditioning on previous text, and a repetition guard (S-1 settings).
* :class:`PiperTts` — Greek and English Piper voices behind a language-run splitter.
* :class:`SoundDeviceIO` — microphone/speaker via PortAudio. Audio never touches disk.
"""

import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from panos_core.voice.ports import FRAME, SAMPLE_RATE, Audio, Transcript
from panos_core.voice.text import sentences, split_language_runs


def _np() -> Any:
    import numpy

    return numpy


def _session(path: Path) -> Any:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


class OnnxWakeWord:
    """Streaming wake-word scorer; feed exactly one FRAME (1280 samples, 80 ms) per call."""

    MEL_WINDOW = 76
    FEATURES = 16

    def __init__(self, model_dir: Path, model: str = "hey_jarvis_v0.1") -> None:
        np = _np()
        self.name = model
        self._mel = _session(model_dir / "melspectrogram.onnx")
        self._emb = _session(model_dir / "embedding_model.onnx")
        self._clf = _session(model_dir / f"{model}.onnx")
        self._clf_input = self._clf.get_inputs()[0].name
        self._np = np
        self.reset()

    def reset(self) -> None:
        np = self._np
        self._raw: deque[int] = deque(maxlen=SAMPLE_RATE * 10)
        self._mel_buf = np.ones((self.MEL_WINDOW, 32), dtype=np.float32)
        # Warm-up features, as openWakeWord does (deterministic here for reproducibility).
        rng = np.random.default_rng(0)
        noise = rng.integers(-1000, 1000, SAMPLE_RATE * 4).astype(np.int16)
        self._features = self._embed_clip(noise)
        self._frames = 0

    def _melspec(self, samples: Audio) -> Audio:
        x = self._np.asarray(samples, dtype=self._np.float32)[None, :]
        out = self._mel.run(None, {"input": x})[0]
        return self._np.squeeze(out) / 10 + 2  # transform used by openWakeWord

    def _embed_clip(self, clip: Audio) -> Audio:
        np = self._np
        spec = self._melspec(clip)
        windows = [
            spec[i : i + self.MEL_WINDOW]
            for i in range(0, spec.shape[0], 8)
            if spec[i : i + self.MEL_WINDOW].shape[0] == self.MEL_WINDOW
        ]
        batch = np.expand_dims(np.array(windows), axis=-1).astype(np.float32)
        return self._emb.run(None, {"input_1": batch})[0].squeeze()

    def score(self, frame: Audio) -> float:
        np = self._np
        if len(frame) != FRAME:
            raise ValueError(f"wake-word frames must be {FRAME} samples")
        self._raw.extend(int(v) for v in frame)
        tail = list(self._raw)[-(FRAME + 160 * 3) :]
        self._mel_buf = np.vstack((self._mel_buf, self._melspec(np.array(tail, np.int16))))
        self._mel_buf = self._mel_buf[-10 * 97 :]
        window = self._mel_buf[-self.MEL_WINDOW :].astype(np.float32)[None, :, :, None]
        embedding = self._emb.run(None, {"input_1": window})[0].squeeze()
        self._features = np.vstack((self._features, embedding))[-120:]
        self._frames += 1
        if self._frames < 5:  # openWakeWord ignores the first frames after a reset
            return 0.0
        x = self._features[-self.FEATURES :][None].astype(np.float32)
        return float(self._clf.run(None, {self._clf_input: x})[0].squeeze())


class SileroVad:
    """Silero VAD v5. Scores a FRAME as the max over its 512-sample chunks."""

    CHUNK = 512
    CONTEXT = 64

    def __init__(self, model_path: Path) -> None:
        self._np = _np()
        self._session = _session(model_path)
        self.reset()

    def reset(self) -> None:
        np = self._np
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(self.CONTEXT, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)

    def is_speech(self, frame: Audio) -> float:
        np = self._np
        audio = np.concatenate([self._pending, np.asarray(frame, np.float32) / 32768.0])
        best = 0.0
        while len(audio) >= self.CHUNK:
            chunk, audio = audio[: self.CHUNK], audio[self.CHUNK :]
            x = np.concatenate([self._context, chunk])[None, :]
            out, self._state = self._session.run(
                None, {"input": x, "state": self._state, "sr": np.array(SAMPLE_RATE, np.int64)}
            )
            self._context = chunk[-self.CONTEXT :]
            best = max(best, float(out.squeeze()))
        self._pending = audio
        return best


class WhisperStt:
    def __init__(
        self,
        model: str | Path,
        *,
        accurate_model: str | Path | None = None,
        threads: int | None = None,
    ) -> None:
        from faster_whisper import WhisperModel

        self._threads = threads or max(1, (os.cpu_count() or 2) // 2)
        self._factory = WhisperModel
        self._model = WhisperModel(
            str(model), device="cpu", compute_type="int8", cpu_threads=self._threads
        )
        self._accurate_name = accurate_model
        self._accurate: Any = None
        self._lock = threading.Lock()

    def transcribe(
        self, audio: Audio, *, language: str | None, prompt: str = "", accurate: bool = False
    ) -> Transcript:
        np = _np()
        model = self._model
        if accurate and self._accurate_name is not None:
            if self._accurate is None:
                self._accurate = self._factory(
                    str(self._accurate_name),
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=self._threads,
                )
            model = self._accurate
        x = np.asarray(audio, dtype=np.float32) / 32768.0  # arrays, never files (S-1 §8)
        started = time.monotonic()
        with self._lock:
            segments, info = model.transcribe(
                x,
                language=language,
                beam_size=1 if not accurate else 5,
                initial_prompt=prompt or None,
                condition_on_previous_text=False,
                # Commands are short: cap tokens by duration and block repeated phrases, so a
                # hallucination loop on near-silence cannot run to the 448-token limit.
                max_new_tokens=min(440, int(len(x) / SAMPLE_RATE * 12) + 16),
                no_repeat_ngram_size=3,
                without_timestamps=True,
                vad_filter=False,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
        return Transcript(
            text=_dedupe_repetitions(text),
            language=info.language,
            language_probability=float(info.language_probability),
            duration_s=len(x) / SAMPLE_RATE,
            stt_s=time.monotonic() - started,
        )


def _dedupe_repetitions(text: str, max_repeats: int = 2) -> str:
    """Whisper repetition guard: collapse a phrase repeated more than ``max_repeats`` times."""
    pattern = re.compile(r"(\b.{3,60}?\b)(?:[\s,.]+\1){" + str(max_repeats) + r",}", re.I)
    return pattern.sub(r"\1", text)


class PiperTts:
    def __init__(self, greek_voice: Path, english_voice: Path) -> None:
        from piper import PiperVoice

        self._voices = {
            "el": PiperVoice.load(str(greek_voice)),
            "en": PiperVoice.load(str(english_voice)),
        }

    def synthesize(self, text: str) -> Iterator[tuple[Audio, int]]:
        for sentence in sentences(text):
            for lang, run in split_language_runs(sentence):
                for chunk in self._voices[lang].synthesize(run):
                    yield chunk.audio_int16_array, chunk.sample_rate


class SoundDeviceIO:
    def __init__(self, device: int | str | None = None) -> None:
        import sounddevice

        self._sd = sounddevice
        self._device = device
        self._stream: Any = None
        self._playing = threading.Event()

    def start(self, on_frame: Callable[[Audio], None]) -> None:
        def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            on_frame(indata[:, 0].copy())

        self._stream = self._sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME,
            device=self._device,
            callback=callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def play(self, audio: Audio, sample_rate: int) -> None:
        self._playing.set()
        self._sd.play(audio, sample_rate)
        while self._playing.is_set() and self._sd.get_stream().active:
            time.sleep(0.02)
        self._sd.stop()
        self._playing.clear()

    def stop_playback(self) -> None:
        self._playing.clear()
