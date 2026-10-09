# ADR-016: Voice stack

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** BR-005, FR-001, FR-002, VOICE, US-001, US-002, US-003, US-004, AC-US001-01..05, AC-US003-01..04, AC-US004-01..03, NFR-PRV-001, NFR-PERF-001, NFR-ACC-003

## Context
An always-on local wake word "Panos", Greek/English/mixed STT, and Greek and English TTS are required. Usage is personal/internal (D-6), so non-commercial and GPL components are acceptable. S-1 (docs/planning/S-1_voice_feasibility.md) measured the CPU-only reference machine:
- openWakeWord takes 5 ms per 80 ms frame (~8 % of one core). A custom model needs Linux/WSL2 training with ~17–19 GB of data.
- Silero VAD (ONNX) takes 0.5 ms per chunk.
- faster-whisper int8 on 8 threads: `small` ≈ 2.7 s per 5 s command at Greek WER 32 % / CER 9 %; `medium` 7.8 s at 16 % / 4 %; `large-v3-turbo` 11 s at 13 % / 3 %.
- Auto language-ID fails on code-switched speech.
- Piper: RTF ≈ 0.1. Greek voices garble English words unless mixed text is split by language.
- No Greek Windows voice is installed.

## Decision
**Process:** voice runs in a separate worker process supervised by the node, with `PYTHONUTF8=1`.

**Ports:** `AudioIO`, `WakeWordDetector`, `VoiceActivityDetector`, `SpeechToText`, `TextToSpeech`.

**Audio:** kept in memory only (AC-US001-05).

**Command routing:** `stop talking` → TTS control; `stop` / `pause` / `resume` → job control.

**Triggers:**
- **Global hotkey / push-to-talk is the primary trigger in the MVP.**
- openWakeWord (ONNX) runs the wake-word detector. A custom **"Panos" / "Hey Panos"** model is trained in a dedicated task (WSL2 or a one-off cloud GPU), validated with ≥ 20 real recordings and a false-accepts-per-hour measurement before it is enabled by default.
- Until then, `hey_jarvis` is available as an opt-in development wake word.
- Optional second-stage verification with whisper-tiny (+~0.4 s) to reduce false accepts on a common name.

**VAD:** Silero VAD via onnxruntime (no torch dependency), 512-sample chunks at 16 kHz.

**STT:**
- **Default:** faster-whisper `small`, `int8`, `cpu_threads` = physical cores, beam 1–2, `language` = user default (`el`), vocabulary `initial_prompt` built from project, tool and IDE names, `condition_on_previous_text=False`, `without_timestamps=True`, VAD-trimmed input, plus a hard timeout and repetition guard.
- **Accurate mode** (opt-in, for dictation and low-confidence re-transcription): `medium` or `large-v3-turbo` int8.
- Auto language-ID is used only for confident (p ≥ 0.8) monolingual utterances.
- Audio is decoded from numpy arrays, not files (avoids the PyAV incompatibility).

**TTS:**
- Piper as the default: `el_GR-joy-medium` (Greek; `el_GR-rapunzelina-low` CC0 as fallback) and `en_US-lessac-medium` (English).
- A **language-run splitter** sends Latin-script runs to the English voice and Greek runs to the Greek voice.
- Sentence-level streaming (first audio ≈ 0.3–0.4 s).
- A WinRT OneCore adapter is optional, available when the user installs the Greek speech pack ("Stefanos").
- Piper (GPL-3.0) stays isolated in its own adapter inside the voice worker.

**Resource coordination:** STT and local LLM generation do not run concurrently (shared scheduler with ADR-009).

## Alternatives considered
Porcupine (AccessKey with online validation, no Greek); Vosk (weak Greek); medium/turbo as default STT (8–11 s per command on CPU); OS-native TTS as default (no Greek voice installed).

## Consequences
- Interactive Greek STT on CPU is usable for commands (fuzzy intent parsing tolerates ~9 % CER) but weak for dictation.
- Wake-word-first hands-free use is deferred until the custom model is trained and validated. AC-US001 is met with a dev wake word initially and with "Panos" after the training task.
- **Follow-ups (M7):** `CT2_USE_MKL=1` A/B; whisper.cpp + Vulkan on the iGPU; real-voice and far-field evaluation; mic-capture latency.
- Licences (personal use): openWakeWord pre-trained models CC BY-NC-SA, joy voice CC BY-NC, lessac non-commercial, Piper GPL-3.0. Revisit if distribution is ever considered.

## Implementation notes (M7, 2026-10-09)

- **Worker:** `python -m panos_core.voice.worker <config.json>` (frozen build: `panos-node voice-worker <config>`). It speaks a JSON-lines protocol on stdin/stdout. Commands: `listen`, `stop_listening`, `cancel_listening`, `speak`, `stop_talking`, `configure`, `shutdown`. Events: `ready`, `state`, `wake`, `transcript`, `no_speech`, `speech_stopped`, `spoken`, `error`.
- **Supervisor:** `VoiceSupervisor` in the node. It restarts a crashed worker with backoff (at most 5 times, then it stops and reports `last_error`), publishes `voice.*` events and routes transcripts:
  - `stop talking` stops TTS;
  - `stop` / `pause` / `resume` act on the most recent active (or, for resume, resumable) job;
  - `mute` mutes the wake word (unmuting is done from the CLI, API or UI);
  - anything else is a conversation turn, with the reply spoken back.
- **Wake word without openwakeword:** the `openwakeword` package needs `tflite-runtime` on Linux, which has no wheels for Python ≥ 3.12. `OnnxWakeWord` reimplements its pipeline (melspectrogram → embedding → classifier) on onnxruntime with the same models. Scores match openwakeword exactly on the S-1 clips (positives 0.998–0.999, negatives ≤ 0.002).
- **VAD:** Silero v5 ONNX with 512-sample chunks plus the 64-sample context the v5 model expects.
- **Endpointing:** an utterance ends after 0.8 s of silence, but only once at least 0.3 s of speech has been heard. A 0.3 s grace period after the wake word (skipping the tail of the wake phrase) also prevents the first live failure, where an utterance ended after 1 s before the user started speaking.
- **STT guards:**
  - `max_new_tokens` is capped from the audio duration, and `no_repeat_ngram_size=3` is set.
  - Live, a repetition loop once produced 31 s of "Παραδείγματος…"; with the guards it no longer can.
  - The vocabulary prompt is built from project and app names.
- **TTS:** Piper. The language-run splitter sends Latin-script runs to the English voice and Greek runs to the Greek voice. Playback stops within one audio block on `stop_talking` or barge-in.
- **Models:**
  - `panos voice setup` downloads files from pinned release tags (openWakeWord v0.5.1, piper-voices v1.0.0, silero-vad v5.1.2) over HTTPS; `--from <dir>` copies an existing folder. **There is no checksum verification yet** (ADR-021 asks for it; open follow-up).
  - Whisper is fetched by faster-whisper itself.
  - Nothing is downloaded implicitly; a missing model is reported in `voice status`.
- **Privacy:** audio is held in numpy buffers only. A test asserts that no audio is written to disk; another checks that the wake word works with networking blocked.
- **Configuration:** `[voice]` in the config (enabled, wake word on/off, model, threshold, language, STT model, accurate model, TTS voices, voice notifications, input device, model dir). `PATCH /voice/settings` applies changes live.
