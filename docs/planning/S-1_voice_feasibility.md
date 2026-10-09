# S-1 Voice & Local-LLM Feasibility Spike — Findings

- **Status:** Complete (time-boxed; some items not measured, see §8)
- **Date:** 2026-10-08
- **Feeds:** ADR-016 (voice stack), ADR-009 / M4-04 (provider abstraction and routing)
- **Prototype location (throwaway):** session scratchpad `…\scratchpad\s1-spike\` (scripts, venv, models, audio, raw JSON results). Nothing in this repo except this note.

---

## 1. Summary (TL;DR)

| Area | Result on this machine (CPU-only, Ryzen 7 PRO 7735U) | Recommendation |
|---|---|---|
| Wake word | openWakeWord `hey_jarvis` (ONNX): **5.0 ms mean / 7.5 ms p95 per 80 ms frame, ≈8 % of one core**, 250 MB RSS, works with network blocked. Synthetic positives scored 0.998–0.999, negatives (incl. Greek speech) ≤ 0.002. | openWakeWord as default. A custom "Panos" model is feasible but needs a Linux (WSL2) training run with ~17 GB of downloads. Until then: hotkey / push-to-talk, plus `hey_jarvis` as a temporary dev wake word. |
| VAD | Silero VAD ONNX: **0.51 ms mean / 0.65 ms p95 per 32 ms chunk** (1 thread). | Silero VAD (ONNX via onnxruntime, without torch). |
| STT | faster-whisper int8, 8 threads. Greek FLEURS (real speech) WER/CER: **small 32 % / 9.0 %**, **medium 15.6 % / 4.4 %**, **large-v3-turbo 12.8 % / 3.2 %**. Latency for a ~4.7 s command (beam 1): **small ≈2.7 s, medium ≈7.8 s, turbo ≈11 s**. | Default **`small` int8, 8 threads, beam 1–2** for interactive commands, with `language="el"` hinting and a vocabulary `initial_prompt`. Offer **medium / large-v3-turbo as an "accurate" mode** (dictation, longer notes) where 2–3× real-time latency is acceptable. CPU-only is the main limitation (see §7.1). |
| TTS | Piper: RTF 0.08–0.12, first audio in ≈0.3–0.4 s. Greek intelligibility proxy (Whisper round-trip CER): **joy-medium 9 %**, rapunzelina-low 12 %, rapunzelina-medium 13 %, English lessac 0 %. Feeding a Greek voice mixed Greek/English text gives garbled English terms (CER 51–79 %). Splitting into language runs (Greek voice + English voice) lowers this to 21 %. **No Greek Windows voice is installed** on this machine. | Piper as the default TTS for both languages: **`el_GR-joy-medium`** (CC BY-NC 4.0, fine for personal use) or `rapunzelina-low` (CC0) as a fallback, plus **`en_US-lessac-medium`**. Add a **language-run splitter** for mixed sentences. OS-native TTS (OneCore "Stefanos") only after the user installs the Greek speech pack. |
| Local LLM (Ollama 0.40.1) | CPU only. The iGPU (Vulkan) is detected but dropped by default (`OLLAMA_IGPU_ENABLE=1` needed; the AMD driver is flagged "too old"). Generation speed: **qwen2.5:3b 11–13 tok/s**, **qwen2.5-coder:7b 5.4–6.6 tok/s**, **qwen2.5-coder:14b 2.7–2.9 tok/s**. Prompt eval: 62 / 24–36 / 12 tok/s. 3B emits structured tool calls. 7B-coder picks the right tool but returns it as JSON text instead of `tool_calls`. | 3B = local intent/tool tier (keep warm). 7B = private but slow coding tier (background jobs). 14B = overnight only. Interactive or agentic coding stays on cloud tiers when policy permits. Add a JSON-in-content tool-call fallback. |

---

## 2. Environment

| Item | Value |
|---|---|
| OS | Windows 11 Enterprise 10.0.26200 |
| CPU | AMD Ryzen 7 PRO 7735U, 8C/16T, base 2.7 GHz (U-series laptop part); CT2 reports AVX2, no AVX-512 |
| GPU | Radeon 680M iGPU (no discrete GPU) |
| RAM | 62 GB (Ollama reported ≈24.8 GiB *available* at start, because normal desktop apps were running: PyCharm, Teams, Chrome, OneDrive) |
| Power | AC, Windows "Balanced" plan. Measurements were taken with normal desktop load, not on a clean bench, so expect ±20–30 % run-to-run noise. |
| Python | 3.12.15 (uv venv in the scratchpad) |
| Packages | faster-whisper 1.2.1, ctranslate2 4.8.2, onnxruntime 1.30.0, openwakeword 0.6.0, silero-vad 6.2.3, piper-tts 1.8.0, torch 2.14.1 (pulled in by silero-vad, not needed with ONNX), jiwer 4.0.0, av 19.0.1 |
| CT2 backend | `Use Intel MKL: false`, int8 GEMM backend DNNL (oneDNN), ISA AVX2. MKL is disabled by default on AMD. |
| Ollama | 0.40.1 (winget `Ollama.Ollama`), server at 127.0.0.1:11434 |

---

## 3. Method

- **Real Greek speech:** 10 utterances from **Google FLEURS `el_gr` test split**, read with HTTP range reads from the HF parquet conversion (`google/fleurs@refs/convert/parquet`, `el_gr/test/0000.parquet`). The references are FLEURS `raw_transcription`. Clip lengths are 6–15 s (101.8 s total).
- **Real English speech:** 4 FLEURS `en_us` test utterances.
- **Synthetic English:** 3 SAPI (David/Zira) technical sentences, rendered at 16 kHz.
- **Synthetic mixed Greek/English (caveat: synthetic):**
  - (a) the whole mixed sentence through a Greek Piper voice. This turned out to mostly measure TTS failure: espeak-ng Greek phonemization mangles English words.
  - (b) **"code-switch" audio:** Greek runs spoken by `el_GR-joy-medium` and Latin-script runs by `en_US-lessac-medium`, concatenated. This is closer to how a Greek developer actually speaks, but it is still TTS. The Greek fragments are also slightly degraded because of short-fragment prosody.
- **Metrics:**
  - WER and CER with `jiwer`, after normalization (lowercase, punctuation removed, Greek diacritics stripped, final sigma normalized).
  - RTF = processing time / audio duration.
  - Language ID from faster-whisper `info.language`.
  - Peak working set from `psutil` (`peak_wset`).
  - Thread count and CPU% sampled every 0.5 s.
- Audio was decoded with soundfile and resampled with scipy (PyAV decode in faster-whisper failed, see §8). Timings therefore exclude file decoding, which matches a live microphone pipeline.
- **Whisper always encodes a 30 s window.** Short utterances therefore pay almost the same encoder cost as 30 s of audio. **Latency per utterance** is the meaningful number for commands. RTF on short clips looks worse than it would on long-form audio.
- Wake word:
  - Clips were padded with 1 s of silence before and after, then fed in 1280-sample (80 ms) frames.
  - CPU cost was measured as process CPU time divided by the number of frames.
  - The offline run monkey-patched `socket.connect` / `create_connection` to raise errors.
- No microphone was used.

---

## 4. Results

### 4.1 STT — accuracy and throughput (faster-whisper, CPU int8, `cpu_threads=8`, beam 5, auto language)

| Model | Load (cold, 1st run incl. download) | Peak RAM (working set) | Threads seen / mean CPU | Greek FLEURS WER / CER | Greek lang-ID | Greek RTF (6–15 s clips) | English FLEURS WER / CER | SAPI English tech (WER) |
|---|---|---|---|---|---|---|---|---|
| small | 2.9 s | **1.0 GB** | 55 / 673 % | 32.2 % / 9.0 % | 10/10 (one at p=0.48) | 0.84 (85 s for 102 s audio) | 9.6 % / 3.2 % | 0 % |
| medium | 68 s* | **2.6 GB** | 71 / 724 % | **15.6 % / 4.4 %** | 10/10 | 2.42 (246 s for 102 s) | 6.1 % / 1.9 % | 0 % |
| large-v3-turbo | 51 s* | **2.0 GB** | 73 / 680 % | **12.8 % / 3.2 %** | 10/10 (p ≥ 0.93) | 2.65 (257 s for 102 s) | 7.4 % / 3.0 % | 0 % |

\*The first load includes the download and conversion caching. Warm loads take a few seconds.

Qualitative Greek output:
- `small` gets the gist but produces many misspelled or near-miss words ("κατεγύδα" for "καταιγίδα", "Αλτράμ" for "Τραμπ").
- `medium` and `turbo` give near-publishable Greek, with only proper nouns occasionally wrong.

### 4.2 STT — interactive latency (Greek, `language="el"`, median of 3)

| Model | Threads | Beam | 4.7 s command | 12.5 s utterance |
|---|---|---|---|---|
| small | 4 | 1 | 3.40 s | 3.87 s |
| small | **8** | **1** | **2.65 s** | 3.99 s |
| small | 8 | 5 | 3.02 s | 5.12 s |
| small | 16 | 1 | 3.37 s | 6.21 s |
| medium | 4 | 1 | 10.84 s | 14.69 s |
| medium | 8 | 1 | 7.80 s | 12.86 s |
| medium | 8 | 5 | 8.37 s | 14.29 s |
| medium | 16 | 1 | 11.52 s | 10.89 s |
| large-v3-turbo | 4 | 1 | 15.83 s | 16.49 s |
| large-v3-turbo | 8 | 1 | 11.09 s | 12.97 s |
| large-v3-turbo | 8 | 5 | 13.42 s | 14.10 s |
| large-v3-turbo | 16 | 1 | 13.30 s | 15.94 s |

- **8 threads (= physical cores) is the sweet spot.** 16 threads (SMT) is slower or noisier.
- Beam 1 saves 10–25 %.
- On CPU, **large-v3-turbo is not faster than medium**. Its encoder is the full large-v3 encoder (32 layers), and the encoder dominates for short clips. The turbo speed-up only appears with GPU or long-form decoding.

### 4.3 STT — mixed Greek/English technical speech (synthetic code-switch audio, 4 sentences)

| Model | Mode | Mean WER | Mean CER | Detected "el" | Median RTF |
|---|---|---|---|---|---|
| small | auto | 45 % | 26 % | 2/4 | 1.13 |
| small | `language="el"` | 48 % | 37 % | 4/4 | 0.59 |
| small | el + vocab `initial_prompt` | 40 % | 23 % | 4/4 | 0.57 |
| medium | auto | 42 % | 22 % | 3/4 | 3.97 |
| medium | el + prompt | **32 %** | **18 %** | 4/4 | 2.40 |
| large-v3-turbo | auto | 39 % | 28 % | 3/4 (one detected **"fi"**) | 5.48 |
| large-v3-turbo | el + prompt | **32 %** | 19 % | 4/4 | 2.31 |

Observations:
- **English technical terms survive well in Latin script.** All models output them correctly: `IntelliJ`, `unit tests`, `PostgreSQL`, `Docker Compose`, `Kubernetes pod`, `commit … branch feature login`, `deployment`. Example (turbo, auto): "Θα commit τις αλλαγές στο branch feature login." Most of the errors are in the short TTS-generated Greek fragments, which is a synthetic-audio artifact.
- **Auto language-ID is unreliable on code-switched utterances.** It flipped to `en` (small 2/4) and once to `fi` (turbo). In "en" mode the Greek words are dropped or transliterated. ⇒ **Do not rely on per-utterance auto-detect for mixed speech.** Use a user-preferred language (`el`), or use detection only when its probability is ≥ ~0.8.
- **A vocabulary `initial_prompt`** listing project, IDE and tech terms improved WER by 8–15 points (for example "Spring Boot" instead of "Fring Boot"). This is cheap to do from the project index.
- **Hallucination risk:** in one medium run (forced `el`, no prompt) the model looped ("Πάνω στον πάνω…") and took 37× real time. Guards needed: VAD-trimmed input, `without_timestamps=True`, a max-tokens / timeout cap, and `condition_on_previous_text=False`.
- Variant (a) (mixed sentence through a Greek Piper voice) gave 45–90 % WER for every model. This reflects Piper's handling of English words, not STT (see §4.5).

### 4.4 Wake word and VAD

| Component | Measurement | Result |
|---|---|---|
| openWakeWord 0.6.0, `hey_jarvis_v0.1.onnx` + mel + embedding (onnxruntime) | per 80 ms frame | **mean 5.04 ms, p50 4.97 ms, p95 7.58 ms, max 9.2 ms** (973 frames) |
| | CPU | **6.4 ms CPU per frame ≈ 8 % of one core** in real time |
| | RAM | 250 MB process RSS (includes Python + numpy/scipy) |
| | model load | 0.21–0.23 s |
| | detection (synthetic) | 4/4 SAPI "Hey Jarvis…" clips → 0.998–0.999. 4 SAPI negatives, 1 English and 2 Greek FLEURS clips → ≤ 0.002 |
| | offline | identical results with sockets blocked (`offline: true`) after the one-time model download (≈8 MB in total) |
| Silero VAD (ONNX, via `silero-vad` 6.2.3) | per 32 ms (512-sample) chunk, 1 thread | **mean 0.51 ms, p95 0.65 ms, max 3.4 ms** |
| Silero VAD (TorchScript) | same | mean 1.87 ms, p95 1.64 ms, max 198 ms (first-call JIT spike) → prefer ONNX |
| "Verify after trigger" option: faster-whisper on a 1.5 s clip (beam 1, en) | tiny 2/4/8 threads | 0.69 / 0.56 / **0.39 s** |
| | base 2/4/8 threads | 1.59 / 0.98 / **0.74 s** |
| | small 2/4/8 threads | 4.9 / 3.3 / 2.6 s |

The ≤ 300 ms wake-feedback budget (NFR-PERF-001) is easily met by openWakeWord (≈ one 80 ms frame plus about 5 ms of inference). A whisper-tiny verification adds ~0.4 s. That is acceptable as a *second-stage* check after the visual "listening" cue, but it does not fit inside the 300 ms budget.

### 4.5 TTS — Piper 1.8.0 (CPU, onnxruntime)

| Voice | Sample rate | License (voice) | Mean RTF | Mean synth time (2–6 s sentences) | First chunk (median) | Load | Round-trip CER, Greek-only text | Round-trip CER, mixed text in one voice |
|---|---|---|---|---|---|---|---|---|
| el_GR-rapunzelina-low | 16 kHz | dataset CC0 | **0.080** | 0.31 s | 0.27 s | 4.8 s | 12 % | 53 % |
| el_GR-rapunzelina-medium | 22.05 kHz | dataset CC0 | 0.118 | 0.40 s | 0.34 s | 4.0 s | 13 % | 79 % |
| el_GR-joy-medium | 22.05 kHz | **CC BY-NC 4.0** | 0.101 | 0.49 s | 0.42 s | 6.5 s | **9 %** | 51 % |
| en_US-lessac-medium | 22.05 kHz | Blizzard 2013 Lessac license (non-commercial research terms) | 0.116 | 0.44 s | 0.38 s | 7.0 s | 0 % (English) | – |
| joy (el runs) + lessac (Latin runs), code-switch | 22.05 kHz | – | – | ≈1.2–1.7 s per sentence (unoptimized, run-by-run) | – | – | – | **21 %** |

The round-trip intelligibility proxy is large-v3-turbo transcription of the Piper output, with the language forced. I did not listen to any audio, so these are subjective-quality proxies only.

Findings:
- All voices are **≥ 8× faster than real time** on CPU. Streaming the first sentence gives about 0.3–0.4 s to first audio.
- **Greek voices mispronounce Latin-script words.** espeak-ng `el` phonemizes "Spring Boot service with PostgreSQL" into garbage ("SPIN BOOTZOVIS…"). A **language-run splitter** (Greek runs → Greek voice, Latin runs → English voice, same 22.05 kHz rate for joy + lessac) is required for Panos replies that contain tech terms.
- **The rapunzelina voices are unstable on short or unterminated fragments.** For example "στο" produced 1.7 s (medium) and 4.9 s (low) of audio, and "Πάνος, άνοιξε το" produced 4.0 s (medium). joy-medium stays stable (0.36 s for "στο"). This matters for the splitter, which produces short fragments, and is another reason to prefer joy.
- All Greek voices mangled the very first word "Καλημέρα" in one sample and read "είκοσι οκτώ" as numerals, which is fine. Greek-only CER of 9–13 % means the speech is intelligible but noticeably synthetic.
- A cosmetic warning appeared: "Missing phoneme from id map: ̧" (rapunzelina-low).

### 4.6 TTS — Windows OS-native voices (checked, nothing installed)

- **Classic SAPI5** (`HKLM:\SOFTWARE\Microsoft\Speech\Voices\Tokens`): David and Zira (en-US) only.
- **OneCore** (`HKLM:\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens`): `MSTTS_V110_enUS_DavidM`, `…MarkM`, `…ZiraM` (en-US only).
- **`Get-InstalledLanguage`:** `el-GR` is installed with **BasicTyping and OCR only, without TextToSpeech or Speech**.
- **How to get Greek ("Microsoft Stefanos", el-GR, male, legacy OneCore voice):** Settings → Time & language → Speech → Manage voices → Add voices → Greek. An alternative is Language & region → Greek → Language options → add *Text-to-speech*. Admin equivalent (not run): `Add-WindowsCapability -Online -Name Language.TextToSpeech~~~el-GR~0.0.1.0`.
- After install, the voice is exposed through **WinRT `Windows.Media.SpeechSynthesis` / OneCore**, not through classic SAPI5 `System.Speech`. An OS-native adapter must therefore use WinRT (as Mark already is OneCore-only here).
- ⚠ PLAN_PROPOSAL R-4 assumes "Windows ships Greek voices". On this managed machine it does not by default, so Greek OS TTS depends on a user or IT action.
- No language packs were installed (as instructed). Stefanos quality was **not measured**.

---

## 5. Custom "Panos" wake word — feasibility assessment

**openWakeWord automatic training** (`notebooks/automatic_model_training.ipynb`, `openwakeword/train.py`):

- **Platform:** the notebook states that *"automated model training is only supported on linux systems"*, because of `piper-sample-generator`. **GPU is strongly recommended.** The notebook estimates "~10 minutes on a free Colab T4" for clip generation at the demo size. Training is PyTorch and runs on CPU too, but it is slow (hours rather than minutes; not measured here).
- **Pinned dependencies:** piper-sample-generator with the `en_US-libritts_r-medium.pt` generator (multi-speaker English), piper-phonemize, webrtcvad, speechbrain 0.5.14, audiomentations 0.33, torch-audiomentations 0.11, tensorflow-cpu 2.8.1, onnx_tf 1.10, datasets 2.14.6, deep-phonemizer, and others. These are old pins: Python 3.8–3.10 era.
- **Data to download:**

  | Dataset | Size |
  |---|---|
  | ACAV100M pre-computed negative features | **16.09 GiB** |
  | False-positive validation features | 0.17 GiB |
  | MIT room impulse responses | small |
  | One AudioSet tar | ≈ 1–2 GB |
  | FMA music | ~1 h used |
  | **Total** | **≈ 17–19 GB** |

- **Config used in the demo:** `n_samples` 1,000 (5,000+ recommended; the default config uses far more), 10,000 steps, target FP ≤ 0.2/h, recall ≥ 0.5.
- **Machine:** WSL2 is present (Ubuntu, WSL v2, currently stopped), and the machine has 62 GB RAM, so a **CPU-only WSL2 training run is plausible**. Estimate: ~1 day of effort including environment fixes, plus several hours of compute. Alternative: use a Colab or GPU box once and copy the resulting `.onnx` (≈ 1 MB) back. Inference on Windows is ONNX-only, which works as shown above.
- **Greek pronunciation risk:**
  - The generator is an *English* multi-speaker TTS. "Πάνος" would have to be approximated with English spellings ("Panos", "Pah-nos", "Pahnoss") and checked by ear.
  - The Greek Piper voices (rapunzelina, joy) could add Greek-accented positives, but they are only 2–3 speakers. That is too little speaker diversity on its own, but useful as an augmentation.
  - Recording ~20–50 real samples from the user would help validation the most.
  - "Panos" is a common name and very short (2 syllables), so expect a higher false-accept rate than with "hey jarvis". A two-syllable phrase such as "Γεια σου Πάνο" or "Hey Panos" would be more robust.
- **Estimated effort:** M (1–2 days) for a first usable model, plus tuning time. **Status: not trained in this spike (time-box).**

**Alternatives:**
- **Porcupine (Picovoice):**
  - Custom keywords are trained in the web Console.
  - It needs a Picovoice **AccessKey** in the SDK. The free tier is for personal / non-commercial use, and the AccessKey is validated against Picovoice servers. This conflicts with a strict "no network" posture; the exact offline grace terms were not verified.
  - Porcupine languages are EN, FR, DE, IT, JA, KO, PT, ES, ZH — **no Greek**. "Panos" would have to be built as an English keyword.
  - Not installed or tested.
- **Fallback with no training:**
  - Keep `VAD → whisper-tiny/base verify`: transcribe 1–1.5 s after a VAD onset and fuzzy-match "πάνο/Πάνος/panos". Cost is 0.39 s (tiny) or 0.74 s (base) per VAD trigger at 8 threads.
  - Running this on *every* speech onset costs several cores in noisy rooms. It is acceptable as a **second-stage verifier** behind openWakeWord, or as a stop-gap behind push-to-talk.
- **Vosk keyword grammar:** not measured. Vosk has no official Greek model of good quality, consistent with ADR-016's "lower Greek accuracy".

---

## 6. Local LLM via Ollama

**Setup:**
- Ollama 0.40.1 was installed via winget. It runs as a tray app, and the server listens on 127.0.0.1:11434.
- Models pulled: `qwen2.5:3b` (1.9 GB), `qwen2.5-coder:7b` (4.7 GB), `qwen2.5-coder:14b` (9.0 GB) and `nomic-embed-text` (274 MB), about 15 GB in total. They are left installed.

**GPU status (from `server.log`):**
- `OLLAMA_VULKAN:true` is already the default in this version.
- Discovery logged `AMD driver is too old. Update your AMD driver to enable GPU inference.` (ROCm path) and `dropping integrated GPU; to enable, set OLLAMA_IGPU_ENABLE=1` for `Vulkan0 "AMD Radeon(TM) Graphics"`.
- ⇒ **All inference below is CPU-only.** `/api/ps` showed `size_vram = 0` for every model.
- An iGPU run with `OLLAMA_IGPU_ENABLE=1` was **not measured** (time-box). It requires restarting the Ollama server with that environment variable. The 680M shares system RAM, so the gain is likely modest for prompt processing and small or none for generation, which is memory-bandwidth bound. This is worth one follow-up run.

**Benchmark** (streaming `/api/generate`, `num_ctx` 4096, `num_predict` 160, temperature 0.2, default threads, model warm after a cold-load call, CPU only):

| Model (Q4_K_M) | Loaded size (`/api/ps`) | Cold load | Prompt eval (code / Greek) | Generation (code / Greek) | TTFT warm (code / Greek) |
|---|---|---|---|---|---|
| qwen2.5:3b | 2.2 GB | 7.5 s | 62 / 64 tok/s | **10.8 / 12.6 tok/s** | 1.1 s / 2.0 s |
| qwen2.5-coder:7b | 5.1 GB | 13.7 s | 36 / 24 tok/s | **5.4 / 6.6 tok/s** | 1.9 s / 5.3 s |
| qwen2.5-coder:14b | 9.9 GB | 26.1 s | 13 / 12 tok/s | **2.9 / 2.7 tok/s** | 5.3 s / 10.3 s |

Prompts: a short Python function plus tests (66 prompt tokens), and a three-sentence Greek explanation of Docker (123 prompt tokens). The script's RSS reading was unusable because the runner process name was not matched, so loaded size is taken from `/api/ps`.

**Quality notes (spot check, not a benchmark):**
- All three models produced correct `is_palindrome` code.
- The Greek answers are understandable but contain grammatical and lexical errors at all sizes, for example "εφαρμόζει την εφαρμογία" (3b) and "ένα πλατφόρματος" (14b).
- Qwen2.5 Greek is weak. Not tested here: Greek-stronger models (Llama 3.1+, Gemma, the Greek fine-tune Meltemi).

**Tool calling** (`/api/chat` with 3 tools: `open_project`, `run_tests`, `get_weather`; temperature 0):

| Model | "Open the panos project in IntelliJ." | Same request in Greek | "Τι καιρό κάνει στην Αθήνα;" | Joke (no tool expected) |
|---|---|---|---|---|
| qwen2.5:3b | ✅ structured `tool_calls` `open_project{project:panos, ide:intellij}` (3.2 s warm, 10.8 s cold) | ✅ same | ✅ `get_weather{city:Athens}` | ✅ plain text |
| qwen2.5-coder:7b | ⚠ **no `tool_calls`**: the correct call was emitted as JSON *text* in `content` | ⚠ same | ⚠ same | ✅ plain text |

- The coder model chose the right function and arguments every time. The Ollama template for `qwen2.5-coder` simply did not produce parseable tool-call tags.
- ⇒ **The ADR-009 adapter needs a fallback parser.** When `tool_calls` is empty and `content` parses as `{"name":…, "arguments":…}` matching a declared tool, treat it as a tool call. The alternative is to select only models flagged `tool_use: native` in the registry.

**Embeddings:** `nomic-embed-text` via `/api/embed` returned 768-dim vectors for 2 short (Greek and English) inputs in 1.22 s cold and **0.12 s warm**. fastembed (ONNX, in-process) is a viable alternative but was not measured.

---

## 7. Recommendations

### 7.1 ADR-016 — voice stack defaults (CPU-only reference machine)

1. **Wake word:**
   - **openWakeWord (ONNX)** in the voice worker, at ≈8 % of one core.
   - Ship with **hotkey / push-to-talk as the primary trigger** until a custom model exists. Optionally run `hey_jarvis` during development.
   - Plan a **separate task to train "Panos" (or "Hey Panos")** in WSL2 or on a cloud GPU (budget: M, ~17–19 GB of data). Validate with ≥ 20 real recordings and a false-accept/hour measurement on a few hours of the user's ambient and meeting audio.
   - Keep the Porcupine adapter as an optional plugin only (AccessKey, online validation, no Greek).
   - Optional second stage: whisper-tiny verification (+0.4 s) to cut false accepts on a common name.
2. **VAD:** **Silero VAD via onnxruntime (ONNX)**, 512-sample chunks at 16 kHz (0.5 ms per chunk). Avoid the torch dependency: load `silero_vad.onnx` directly. openWakeWord already bundles one.
3. **STT:**
   - **Default: faster-whisper `small`, `compute_type=int8`, `cpu_threads=8` (physical cores), beam 1–2, `language` = user default (`el`), `condition_on_previous_text=False`, `without_timestamps=True`, `initial_prompt` built from known project / tool names, VAD-trimmed input and a hard timeout.**
   - Expected cost: ≈2.7 s per 5 s command, 1 GB RAM, Greek CER ≈9 % (WER ≈30 %). That is good enough for intent and command parsing, which can be fuzzy, but weak for dictation.
   - **"Accurate" mode:** `medium` int8 (Greek WER 16 %, ≈8 s per short command, 2.6 GB) or `large-v3-turbo` int8 (Greek WER 13 %, ≈11 s, 2.0 GB). Use it for dictation, long notes, or a re-transcription pass when the small result has low confidence. On CPU, turbo has no speed advantage over medium. Choose turbo for the best accuracy, medium for slightly lower latency.
   - **Do not trust auto language-ID on code-switched speech.** Use auto-detect only for a confident (p ≥ 0.8) monolingual utterance. Otherwise default to `el`, which still outputs English terms correctly in Latin script.
   - Keep the `SpeechToText` port model-agnostic so that whisper.cpp (Vulkan iGPU) or a future GPU machine can drop in. Interactive latency for medium/turbo needs a GPU (R-4 mitigation stands).
   - Spike gap: CT2 int8 on AMD uses oneDNN (MKL disabled). An `CT2_USE_MKL=1` A/B and whisper.cpp + Vulkan on the 680M were **not measured** and are worth a follow-up, since STT latency is the weakest link.
4. **TTS:**
   - **Piper as the default for both languages:** Greek **`el_GR-joy-medium`** (best intelligibility, stable on short fragments, CC BY-NC 4.0 is OK for personal use), with `el_GR-rapunzelina-low` (CC0) as a permissive fallback, and English **`en_US-lessac-medium`**.
   - Sentence-level streaming gives ~0.3–0.4 s to first audio.
   - **Mandatory language-run splitter:** Latin-script runs go to the English voice, Greek runs to the Greek voice. Both are 22.05 kHz, so they can be concatenated without resampling. Without it, tech terms are unintelligible (CER 51 % → 21 %).
   - OS-native adapter (WinRT OneCore "Stefanos"): optional and only if installed. It is **not** the default, because no Greek voice is present on this machine.
   - Licence note: `piper-tts` ≥ 1.3 is **GPL-3.0-or-later** (OHF-Voice/piper1-gpl). That is acceptable for personal use per D-6, but keep Piper in its own adapter or process boundary.
5. **Resource budget for the voice worker** (all CPU):
   - Idle (wake word + VAD): ≈ 0.1 core, ~250–300 MB.
   - During a command: 8 cores for ~3 s (small) and ~1 GB.
   - TTS: ~0.5 s of a few cores per sentence.
   - This competes with a local LLM on the same 8 cores, so the router should avoid running local LLM generation and STT at the same moment, or accept the slowdown.

### 7.2 ADR-009 / routing

Realistic local tiers on this CPU-only laptop (Q4_K_M, Ollama, 8 cores):

| Tier | Model class | Measured speed | Realistic use |
|---|---|---|---|
| **Lightweight / intent** | 3B (qwen2.5:3b) | 11–13 tok/s gen, ~60 tok/s prompt, TTFT 1–2 s, 2.2 GB | Intent classification and tool selection (structured tool calls worked, including from Greek input), short English replies, short summaries. Meets the NFR-PERF-002 < 1.5 s budget only for **very short outputs** (a small JSON intent) with the model kept warm. |
| **Local capable / coding** | 7B coder | 5–7 tok/s gen, 24–36 tok/s prompt, 5.1 GB | Single-function code, small edits, commit messages, privacy-restricted work. A 300-token answer takes ~1 min, and a 4k-token context takes ~2–3 min just to ingest. **Not viable for interactive agentic multi-file coding.** |
| **Local large** | 14B coder | ~2.8 tok/s gen, ~12 tok/s prompt, 9.9 GB | Background or overnight jobs only (4k-token context ≈ 5–6 min of prompt eval). |
| 30B+ / MoE | – | not measured | RAM allows it. Dense models would likely run at ≤ 1–2 tok/s. An MoE such as qwen3-30b-a3b may approach 3B speed with better quality; this is a follow-up candidate. |

Consequences for ADR-009 / the M4-04 router:
1. **Capability registry entries need per-machine `prompt_tps`, `gen_tps` and `ttft`.** Seed them from this table and refresh with a micro-benchmark on health check. The router's latency score must include **prompt length**, because prompt eval at 12–36 tok/s dominates for coding contexts.
2. **Local-only coding is "slow but private".** When policy forces local execution, the router should:
   - explain the expected slowness;
   - prefer the 7B tier with aggressively trimmed context;
   - run the work as a background Job with progress events.

   Cloud tiers remain the default for interactive coding where policy permits.
3. **Tool-call fallback** for models that emit JSON in content (qwen2.5-coder). The registry field should be `tool_use: native | json_in_content | none`.
4. **CPU contention with voice:**
   - STT (8 threads) and LLM generation share the same 8 cores. Do not start local generation while an utterance is being transcribed, or cap `num_thread`.
   - Keep the 3B intent model warm and unload larger models after use (`keep_alive`). Only ~25 GB of RAM was actually free during normal desktop use.
5. **Greek:** local Qwen2.5 models produce flawed Greek prose but handle Greek *input* fine for intent and JSON. For Greek user-facing replies, either evaluate a Greek-capable local model (follow-up) or allow cloud for non-sensitive replies.
6. **iGPU:** treat it as unsupported by default. Revisit with `OLLAMA_IGPU_ENABLE=1` and an updated AMD driver; this is a cheap follow-up.

---

## 8. Issues encountered, gaps, and what is *not* measured

- **PyAV incompatibility:** faster-whisper 1.2.1 calls `av.open(..., metadata_errors=…)`, which fails with av 19.0.1 (`TypeError`). Workaround: decode with soundfile and pass numpy arrays, which the voice worker would do anyway. Pin `av<…` or avoid file decoding.
- **Windows console encoding:** cp1252 broke Greek prints. Set `PYTHONUTF8=1` in the worker.
- **`silero-vad` pulls in torch (≈ 2+ GB install).** Use ONNX directly.
- **Synthetic audio:** all mixed Greek/English speech and all wake-word positives are TTS (SAPI and Piper), not real voices. Real accented, noisy and far-field audio will score worse. Greek accuracy numbers come from read speech (FLEURS), not spontaneous commands.
- **Not measured:**
  - A custom "Panos" model: not trained (time-box; Linux and 17 GB of data needed).
  - False-accept rate per hour: there was no long ambient corpus.
  - Porcupine: needs an AccessKey/account.
  - Vosk.
  - Quality of the Windows "Stefanos" voice: language pack not installed by instruction.
  - The CT2 MKL A/B, whisper.cpp/Vulkan and distil-whisper: there is no Greek distil model.
  - Listening tests of TTS: Whisper round-trip CER was used as a proxy.
  - Microphone capture latency.
  - LLM gaps are noted in §6.
- **Noise:** the measurements ran next to normal desktop apps. The latency sweep showed ±20–30 % variance, for example small at 16 threads ranged from 3.4 to 6.2 s.
- **Disk:** about 242 MB of Piper voice downloads went to the default HF cache `%USERPROFILE%\.cache\huggingface\hub\` before `HF_HOME` was redirected to the scratchpad. They can be deleted manually. Everything else is under the scratchpad, plus Ollama models in `%USERPROFILE%\.ollama\models` (≈15 GB).

---

## 9. Licences

| Component | Licence | Note |
|---|---|---|
| faster-whisper 1.2.1 | MIT | |
| CTranslate2 4.8.2 | MIT | |
| Whisper weights (small/medium/large-v3-turbo; Systran/mobiuslabs CT2 conversions) | MIT (OpenAI Whisper) | |
| openWakeWord 0.6.0 code | Apache-2.0 | |
| openWakeWord pre-trained models (`hey_jarvis`, etc.) | **CC BY-NC-SA 4.0** | Personal use OK. A self-trained "Panos" model's licence depends on its training data (ACAV100M features, AudioSet, FMA have mixed or NC terms). |
| Silero VAD 6.x | MIT | |
| onnxruntime | MIT | |
| piper-tts 1.8.0 | **GPL-3.0-or-later** (OHF-Voice/piper1-gpl; bundles espeak-ng data, GPL) | Isolate behind an adapter or process. |
| el_GR-rapunzelina (low/medium) | Dataset CC0 (Kaggle Greek single-speaker) | Most permissive Greek option |
| el_GR-joy-medium | **CC BY-NC 4.0** | Personal / non-commercial only |
| en_US-lessac-medium | Blizzard 2013 Lessac dataset licence (non-commercial research) | Fine for personal use. Swap for a permissive voice (e.g. a LibriTTS-based voice) if Panos is ever distributed. |
| Ollama | MIT | |
| Qwen2.5 / Qwen2.5-Coder 3B | Qwen Research licence (3B: non-commercial research) | 7B and 14B are Apache-2.0 |
| nomic-embed-text v1.5 | Apache-2.0 | |
| Porcupine | Proprietary, AccessKey, free personal tier | Not used |
| Google FLEURS (test audio only) | CC BY 4.0 | |
