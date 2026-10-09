"""Catalogue of ``/ws/events`` event types and their payloads (ADR-018, M7-02).

The desktop's TypeScript event types are generated from this file
(``scripts/export_event_types.py``) and CI fails if they drift. Field types use TypeScript
notation; ``?`` marks optional fields.
"""

from typing import Final

EVENT_TYPES: Final[dict[str, dict[str, str]]] = {
    "conversation.message": {
        "conversation_id": "string",
        "message_id": "string",
        "role": '"user" | "assistant"',
        "intent": "string",
        "provider?": "string | null",
        "model?": "string | null",
        "locality?": "string | null",
    },
    "conversation.delta": {"conversation_id": "string", "text": "string"},
    "job.status": {
        "job_id": "string",
        "status": "JobStatus",
        "reason": "string",
        "project_id": "string | null",
        "kind": "string",
    },
    "notification": {
        "kind": '"approval" | "blocked" | "question" | "completed" | "failed" | "cancelled"',
        "title": "string",
        "body": "string",
        "project_id": "string | null",
        "priority": '"normal" | "high"',
    },
    "voice.ready": {"wake_word": "string | null", "tts": "boolean"},
    "voice.state": {"state": '"idle" | "listening" | "transcribing" | "muted"'},
    "voice.wake": {"score": "number", "detector": "string"},
    "voice.transcript": {
        "text": "string",
        "language": "string",
        "language_probability": "number",
        "duration_s": "number",
        "stt_s": "number",
        "trigger": "string",
    },
    "voice.no_speech": {"trigger": "string"},
    "voice.spoken": {"seconds": "number"},
    "voice.speech_stopped": {},
    "voice.reply": {"text": "string"},
    "voice.error": {"detail": "string", "fatal?": "boolean"},
}

JOB_STATUSES: Final = (
    "QUEUED",
    "PLANNING",
    "RUNNING",
    "WAITING_FOR_TOOL",
    "WAITING_FOR_APPROVAL",
    "WAITING_FOR_USER",
    "PAUSED",
    "BLOCKED",
    "FAILED",
    "COMPLETED",
    "CANCELLED",
)
