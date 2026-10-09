# Wake word "Panos" / "Hey Panos": training and validation (M7-07a, ADR-016)

**Status:** tooling ready; training and validation need the owner (real recordings) and a
one-off training run. Until the targets below are met, push-to-talk (global hotkey) stays the
primary trigger and `hey_jarvis` is the opt-in development wake word.

## Targets (enable by default only if all are met)

| Metric | Target |
|---|---|
| Real positive recordings | ≥ 20 (several sessions, distances, tones; laptop mic) |
| True-accept rate on them | ≥ 90 % |
| False accepts | ≤ 0.5 per hour of normal desk audio (speech, typing, calls, Greek and English) |

## 1. Record (owner, ~30 min)

```powershell
uv run python scripts/wakeword/record.py positives C:\ww\positives --count 25 --phrase "Panos"
uv run python scripts/wakeword/record.py positives C:\ww\positives-hey --count 10 --phrase "Hey Panos"
uv run python scripts/wakeword/record.py ambient C:\ww\ambient --minutes 60   # during normal work
```

The recordings stay in the folders you choose (they are personal data; delete them after
validation). Panos itself never stores audio.

## 2. Train (one-off; WSL2 Ubuntu or a rented GPU)

openWakeWord's automatic training pipeline (`openwakeword/train.py`, the official
"automatic_model_training" notebook) generates thousands of synthetic positives with Piper
(Greek and English voices), mixes them with background noise and negative speech corpora
(~17–19 GB of downloads, measured in S-1), and trains the small classifier on top of the
shared embedding model. Steps:

1. WSL2 Ubuntu 22.04+, Python 3.10/3.11 (tflite and the training stack are Linux-only).
2. Clone `dscripka/openWakeWord`, install `.[full]` and the piper-sample-generator.
3. Config: target phrases `["panos", "hey panos"]`, Greek + English Piper voices, 50k–100k
   synthetic samples, standard negative data (ACAV100M features, FMA/audioset noise).
4. Train (CPU: hours; a single consumer GPU: well under an hour). Export ONNX.
5. Copy `panos_v1.onnx` to `<data>/models/voice/wakeword/`.

## 3. Validate (owner's recordings, never the synthetic training data)

```powershell
uv run python scripts/wakeword/validate.py --models "$env:LOCALAPPDATA\Panos\models\voice" `
    --model panos_v1 --positives C:\ww\positives --negatives C:\ww\ambient --report ww-report.md
```

If a threshold meets the targets: `voice.wake_word_model = "panos_v1"`, `voice.threshold = <t>`
in the config, and record the report in `docs/planning/reports/`. Otherwise iterate on data
(more varied positives, harder negatives such as "πάνω", "pan", "Πάνος" in normal speech) or
add the optional second-stage whisper-tiny verification (ADR-016).
