"""Record wake-word validation samples (M7-07a). Explicit, opt-in: files are written only to
the folder you pass, never by Panos itself.

Usage:
  uv run python scripts/wakeword/record.py positives <folder> --count 25 --phrase "Panos"
  uv run python scripts/wakeword/record.py ambient <folder> --minutes 30

Positives: say the phrase once per beep, varying distance, speed and tone (the ADR asks for
≥ 20 real recordings). Ambient: leave it running during normal work (speech, typing, calls)
to measure false accepts per hour.
"""

import argparse
import time
import wave
from pathlib import Path

SAMPLE_RATE = 16_000


def _write(path: Path, audio: "object") -> None:
    import numpy as np

    data = np.asarray(audio, dtype=np.int16).tobytes()
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(data)


def positives(folder: Path, count: int, phrase: str, seconds: float) -> None:
    import sounddevice as sd

    folder.mkdir(parents=True, exist_ok=True)
    print(f"Say “{phrase}” after each beep ({count} takes). Ctrl+C to stop.")
    for n in range(1, count + 1):
        input(f"[{n}/{count}] press Enter, then speak…")
        print("\a", end="", flush=True)
        audio = sd.rec(
            int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16"
        )
        sd.wait()
        target = folder / f"positive_{n:03d}.wav"
        _write(target, audio[:, 0])
        print(f"   saved {target.name}")


def ambient(folder: Path, minutes: float) -> None:
    import sounddevice as sd

    folder.mkdir(parents=True, exist_ok=True)
    chunk_s = 60
    total = int(minutes * 60 / chunk_s)
    print(f"Recording {minutes:g} min of ambient audio in {chunk_s}s files. Ctrl+C to stop.")
    for n in range(1, total + 1):
        audio = sd.rec(chunk_s * SAMPLE_RATE, samplerate=SAMPLE_RATE, channels=1, dtype="int16")
        sd.wait()
        _write(folder / f"ambient_{time.strftime('%Y%m%d_%H%M%S')}_{n:03d}.wav", audio[:, 0])
        print(f"   {n}/{total}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("kind", choices=["positives", "ambient"])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--count", type=int, default=25)
    parser.add_argument("--phrase", default="Panos")
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--minutes", type=float, default=30.0)
    args = parser.parse_args()
    if args.kind == "positives":
        positives(args.folder, args.count, args.phrase, args.seconds)
    else:
        ambient(args.folder, args.minutes)


if __name__ == "__main__":
    main()
