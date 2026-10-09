# Voice Subsystem

## Requirements
- Local always-on wake-word `Panos`.
- Configurable global hotkey/push-to-talk.
- Greek, English and mixed technical language.
- Local/offline STT and TTS options.
- Pluggable cloud STT/TTS where privacy policy permits.
- Written response always available; voice response configurable.

## Pipeline
Microphone → local wake-word → VAD → capture utterance → STT → orchestrator → response → text UI → TTS.

## Commands
- `Panos ...` activate
- `stop talking` cancel TTS only
- `stop` cancel active task when safe
- `pause` pause job
- `resume` resume job

## Privacy
Ambient audio is processed locally for wake-word detection and not stored by default. Utterance audio is ephemeral unless the user explicitly enables history/diagnostics.

## Notifications
For long jobs, speak only important approval/blocker/completion notifications by default.
