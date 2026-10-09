"""Validate a wake-word model against real recordings (M7-07a, ADR-016).

Measures, per threshold:
* true-accept rate on positive clips (each clip must trigger at least once);
* false accepts per hour on ambient/negative audio (detections with a 2 s refractory period).

The model is enabled by default only if it meets the targets in ``docs/planning/
wakeword-training.md`` (TA ≥ 90 % on ≥ 20 real recordings and ≤ 0.5 false accepts per hour).

Usage:
  uv run python scripts/wakeword/validate.py --models <voice model dir> --model panos_v1 \\
      --positives <folder> --negatives <folder> [--report report.md]
"""

import argparse
import wave
from pathlib import Path
from typing import Any

from panos_core.voice.adapters import OnnxWakeWord

FRAME = 1280
TARGET_TA = 0.90
TARGET_FA_PER_HOUR = 0.5


def load(path: Path) -> Any:
    import numpy as np

    with wave.open(str(path), "rb") as f:
        if f.getframerate() != 16_000 or f.getnchannels() != 1 or f.getsampwidth() != 2:
            raise ValueError(f"{path.name}: expected 16 kHz mono 16-bit WAV")
        return np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)


def scores(detector: OnnxWakeWord, audio: Any) -> list[float]:
    import numpy as np

    padded = np.concatenate([np.zeros(16_000, np.int16), audio, np.zeros(16_000, np.int16)])
    detector.reset()
    return [detector.score(padded[i : i + FRAME]) for i in range(0, len(padded) - FRAME, FRAME)]


def detections(frame_scores: list[float], threshold: float, refractory_frames: int = 25) -> int:
    count, cooldown = 0, 0
    for s in frame_scores:
        if cooldown:
            cooldown -= 1
        elif s >= threshold:
            count, cooldown = count + 1, refractory_frames
    return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--models", type=Path, required=True, help="voice model directory")
    parser.add_argument("--model", required=True, help="wake-word model name (without .onnx)")
    parser.add_argument("--positives", type=Path, required=True)
    parser.add_argument("--negatives", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    detector = OnnxWakeWord(args.models / "wakeword", args.model)
    pos = [scores(detector, load(p)) for p in sorted(args.positives.glob("*.wav"))]
    neg = [scores(detector, load(p)) for p in sorted(args.negatives.glob("*.wav"))]
    hours = sum(len(s) for s in neg) * FRAME / 16_000 / 3600
    if len(pos) < 20:
        print(f"warning: only {len(pos)} positive recordings (ADR-016 asks for ≥ 20)")
    rows = []
    for threshold in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        ta = sum(detections(s, threshold) > 0 for s in pos) / max(1, len(pos))
        fa = sum(detections(s, threshold) for s in neg) / hours if hours else float("nan")
        ok = ta >= TARGET_TA and fa <= TARGET_FA_PER_HOUR and len(pos) >= 20
        rows.append((threshold, ta, fa, ok))
    lines = [
        f"# Wake-word validation: {args.model}",
        "",
        f"Positives: {len(pos)} clips · negatives: {hours:.2f} h",
        "",
        "| threshold | true accepts | false accepts / h | meets targets |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {t:.1f} | {ta:.0%} | {fa:.2f} | {'yes' if ok else 'no'} |" for t, ta, fa, ok in rows
    ]
    best = next((r for r in rows if r[3]), None)
    lines += [
        "",
        "**Recommendation:** "
        + (
            f"enable with threshold {best[0]:.1f}."
            if best
            else "do not enable by default (targets not met); keep push-to-talk."
        ),
    ]
    text = "\n".join(lines) + "\n"
    print(text)
    if args.report:
        args.report.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
