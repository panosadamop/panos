"""Voice model files: locations and one-time provisioning (``panos voice setup``).

Models live in ``<data>/models/voice``. Provisioning downloads them from their upstream
release locations or copies them from a local directory (``--from``). Nothing is fetched at
run time: the worker only loads files that are already present (offline operation).
"""

import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Final

OWW: Final = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
PIPER: Final = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"
SILERO: Final = "https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data"


@dataclass(frozen=True)
class ModelFile:
    rel: str  # path under the voice model directory
    url: str
    required: bool = True  # wake-word files are optional (push-to-talk works without them)


FILES: Final = (
    ModelFile("vad/silero_vad.onnx", f"{SILERO}/silero_vad.onnx"),
    ModelFile("piper/el_GR-joy-medium.onnx", f"{PIPER}/el/el_GR/joy/medium/el_GR-joy-medium.onnx"),
    ModelFile(
        "piper/el_GR-joy-medium.onnx.json",
        f"{PIPER}/el/el_GR/joy/medium/el_GR-joy-medium.onnx.json",
    ),
    ModelFile(
        "piper/en_US-lessac-medium.onnx", f"{PIPER}/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
    ),
    ModelFile(
        "piper/en_US-lessac-medium.onnx.json",
        f"{PIPER}/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
    ),
    ModelFile("wakeword/melspectrogram.onnx", f"{OWW}/melspectrogram.onnx", required=False),
    ModelFile("wakeword/embedding_model.onnx", f"{OWW}/embedding_model.onnx", required=False),
    ModelFile("wakeword/hey_jarvis_v0.1.onnx", f"{OWW}/hey_jarvis_v0.1.onnx", required=False),
)
WHISPER_DIR: Final = "whisper"  # faster-whisper model folders: whisper/<size>


def voice_dir(data_dir: Path) -> Path:
    return data_dir / "models" / "voice"


def missing(model_dir: Path, whisper_size: str) -> list[str]:
    out = [f.rel for f in FILES if f.required and not (model_dir / f.rel).is_file()]
    if not (model_dir / WHISPER_DIR / whisper_size / "model.bin").is_file():
        out.append(f"{WHISPER_DIR}/{whisper_size}")
    return out


def wake_word_available(model_dir: Path, model: str = "hey_jarvis_v0.1") -> bool:
    return all(
        (model_dir / "wakeword" / n).is_file()
        for n in ("melspectrogram.onnx", "embedding_model.onnx", f"{model}.onnx")
    )


def provision(
    model_dir: Path, *, whisper_size: str = "small", source: Path | None = None
) -> list[str]:
    """Download (or copy from ``source``) every missing file. Returns what was added."""
    added = []
    for f in FILES:
        target = model_dir / f.rel
        if target.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        candidates = [source / Path(f.rel).name, source / f.rel] if source else []
        local = next((c for c in candidates if c.is_file()), None)
        if local is not None:
            shutil.copyfile(local, target)
        else:
            tmp = target.with_suffix(target.suffix + ".part")
            urllib.request.urlretrieve(f.url, tmp)  # noqa: S310 - fixed https URLs above
            tmp.replace(target)
        added.append(f.rel)
    whisper = model_dir / WHISPER_DIR / whisper_size
    if not (whisper / "model.bin").is_file():
        from faster_whisper import download_model

        download_model(whisper_size, output_dir=str(whisper))
        added.append(f"{WHISPER_DIR}/{whisper_size}")
    return added
