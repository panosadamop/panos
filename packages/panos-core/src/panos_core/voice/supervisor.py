"""Node-side voice supervisor (ADR-016, VOICE.md, US-001..004, US-051).

* Starts the voice worker as a child process (``PYTHONUTF8=1``), restarts it after a crash
  with backoff, and stops it with the node.
* Routes transcripts: ``stop talking`` → silence speech; ``stop`` / ``pause`` / ``resume``
  → the most recent active job; anything else → the conversation, whose reply is spoken.
* Speaks only important notifications (approvals, questions, blockers, completion,
  failure); routine progress is never narrated (AC-US051-02).
* Publishes every worker event on the event bus as ``voice.<event>``.
"""

import asyncio
import contextlib
import json
import os
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from panos_core.config.settings import VoiceSettings
from panos_core.domain.enums import JobStatus
from panos_core.events import EventBus
from panos_core.observability import get_logger
from panos_core.voice.models import missing, voice_dir
from panos_core.voice.text import route

log = get_logger(__name__)

MAX_RESTARTS: Final = 5
RESTART_WINDOW_S: Final = 300.0
_ACTIVE: Final = (JobStatus.RUNNING, JobStatus.PLANNING, JobStatus.WAITING_FOR_TOOL)
_RESUMABLE: Final = (JobStatus.PAUSED, JobStatus.BLOCKED, JobStatus.WAITING_FOR_USER)

Reply = Callable[[str, str | None], Awaitable[tuple[str, str]]]  # text, conv → reply, conv


def worker_command(config: Path) -> list[str]:
    """``python -m …`` in development; the packaged node runs ``panos-node voice-worker``."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "voice-worker", str(config)]
    return [sys.executable, "-m", "panos_core.voice.worker", str(config)]


@dataclass
class VoiceStatus:
    running: bool = False
    state: str = "stopped"
    wake_word: str | None = None
    last_transcript: str | None = None
    last_error: str | None = None
    restarts: int = 0
    missing_models: list[str] = field(default_factory=list)


class VoiceSupervisor:
    def __init__(
        self,
        settings: VoiceSettings,
        data_dir: Path,
        runtime_dir: Path,
        events: EventBus,
        reply: Reply,
        jobs: Any,
        runner: Any,
        vocabulary: Callable[[], Awaitable[list[str]]],
    ) -> None:
        self.settings = settings
        self.model_dir = settings.model_dir or voice_dir(data_dir)
        self._runtime_dir = runtime_dir
        self._events = events
        self._reply = reply
        self._jobs = jobs
        self._runner = runner
        self._vocabulary = vocabulary
        self.status = VoiceStatus()
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stopping = False
        self._crashes: list[float] = []
        self._conversation_id: str | None = None
        self._handlers: set[asyncio.Task[str]] = set()

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> VoiceStatus:
        if self._proc is not None and self._proc.returncode is None:
            return self.status
        self.status.missing_models = missing(self.model_dir, self.settings.stt_model)
        if self.status.missing_models:
            self.status.last_error = "voice models are missing: run `panos voice setup`"
            self.status.running, self.status.state = False, "stopped"
            return self.status
        self._stopping = False
        config = await self._config()
        path = self._runtime_dir / "voice-worker.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding="utf-8")
        env = {**os.environ, "PYTHONUTF8": "1", "HF_HUB_OFFLINE": "1"}
        self._proc = await asyncio.create_subprocess_exec(
            *worker_command(path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self.status.running, self.status.state = True, "starting"
        self._reader = asyncio.create_task(self._read(self._proc))
        log.info("voice_worker_started", pid=self._proc.pid)
        return self.status

    async def stop(self) -> None:
        self._stopping = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            await self._send({"cmd": "shutdown"})
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
        for task in list(self._handlers):
            task.cancel()
        self._proc = None
        self.status.running, self.status.state = False, "stopped"

    async def _config(self) -> dict[str, Any]:
        s = self.settings
        words = await self._vocabulary()
        return {
            "model_dir": str(self.model_dir),
            "stt_model": s.stt_model,
            "accurate_model": s.accurate_model,
            "wake_word_model": s.wake_word_model,
            "input_device": s.input_device,
            "settings": {
                "wake_word": s.wake_word,
                "threshold": s.threshold,
                "language": s.language,
                "tts": s.tts,
                "prompt": ", ".join(words)[:800],
            },
        }

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None  # noqa: S101 - created with PIPE
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            await self._on_event(event)
        code = await proc.wait()
        stderr = (
            (await proc.stderr.read()).decode("utf-8", "replace")[-2000:] if proc.stderr else ""
        )
        self.status.running, self.status.state = False, "stopped"
        if not self._stopping:
            await self._crashed(code, stderr)

    async def _crashed(self, code: int, stderr: str) -> None:
        now = time.monotonic()
        self._crashes = [t for t in self._crashes if now - t < RESTART_WINDOW_S] + [now]
        self.status.restarts += 1
        self.status.last_error = f"voice worker exited ({code}): {stderr.strip()[-300:]}"
        log.warning("voice_worker_crashed", code=code, restarts=len(self._crashes))
        self._events.publish("voice.error", {"detail": self.status.last_error, "fatal": True})
        if len(self._crashes) <= MAX_RESTARTS:
            await asyncio.sleep(min(30.0, 2.0 ** len(self._crashes)))
            if not self._stopping:
                self._proc = None
                await self.start()

    # ------------------------------------------------------------------ commands
    async def _send(self, message: dict[str, Any]) -> bool:
        proc = self._proc
        if proc is None or proc.returncode is not None or proc.stdin is None:
            return False
        proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        await proc.stdin.drain()
        return True

    async def listen(self) -> bool:
        return await self._send({"cmd": "listen", "trigger": "push_to_talk"})

    async def stop_listening(self) -> bool:
        return await self._send({"cmd": "stop_listening"})

    async def speak(self, text: str) -> bool:
        return await self._send({"cmd": "speak", "text": text})

    async def stop_talking(self) -> bool:
        return await self._send({"cmd": "stop_talking"})

    async def configure(self, **changes: Any) -> bool:
        for key, value in changes.items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)
        mapped = {
            k: v
            for k, v in changes.items()
            if k in ("wake_word", "threshold", "language", "tts", "muted")
        }
        return await self._send({"cmd": "configure", "settings": mapped})

    # ------------------------------------------------------------------ events
    async def _on_event(self, event: dict[str, Any]) -> None:
        kind = str(event.get("event", ""))
        payload = {k: v for k, v in event.items() if k != "event"}
        self._events.publish(f"voice.{kind}", payload)
        if kind == "state":
            self.status.state = str(event.get("state"))
        elif kind == "ready":
            self.status.wake_word = event.get("wake_word")
        elif kind == "error":
            self.status.last_error = str(event.get("detail"))
        elif kind == "transcript":
            text = str(event.get("text", "")).strip()
            self.status.last_transcript = text
            if text:
                task = asyncio.create_task(self.handle_transcript(text))
                self._handlers.add(task)
                task.add_done_callback(self._handlers.discard)

    async def handle_transcript(self, text: str) -> str:
        """Route one utterance. Returns what happened (for events and tests)."""
        routed = route(text)
        if routed.command == "stop_talking":
            await self.stop_talking()
            return "stopped talking"
        if routed.command == "mute":
            await self.configure(muted=True)
            return "muted"
        if routed.command == "cancel_listening" or not routed.text:
            return "ignored"
        if routed.command in ("stop", "pause", "resume"):
            outcome = await self._job_control(routed.command)
            await self.speak(outcome)
            return outcome
        try:
            answer, self._conversation_id = await self._reply(routed.text, self._conversation_id)
        except Exception as exc:
            log.warning("voice_reply_failed", error=type(exc).__name__)
            answer = (
                "Δεν μπόρεσα να απαντήσω."
                if self.settings.language == "el"
                else "I could not answer that."
            )
        self._events.publish("voice.reply", {"text": answer})
        await self.speak(answer)
        return answer

    async def _job_control(self, command: str) -> str:
        jobs = await self._jobs.list(limit=50)
        if command == "resume":
            target = next((j for j in jobs if j.status in _RESUMABLE), None)
            if target is None:
                return "No job to resume."
            await self._runner.resume(target.id, actor="user:voice")
            return f"Resuming: {target.objective[:60]}"
        target = next((j for j in jobs if j.status in _ACTIVE), None)
        if target is None:
            return "No job is running."
        if command == "pause":
            await self._runner.pause(target.id, actor="user:voice")
            return f"Pausing at the next safe point: {target.objective[:60]}"
        await self._runner.cancel(target.id, actor="user:voice")
        return f"Cancelled: {target.objective[:60]}"


@dataclass
class VoiceChannel:
    """Notification channel: speaks only what needs the user (AC-US051-01/02)."""

    supervisor: VoiceSupervisor
    name: str = "voice"

    async def send(self, notification: Any) -> None:
        if not (self.supervisor.settings.voice_notifications and self.supervisor.status.running):
            return
        if notification.kind not in ("approval", "question", "blocked", "completed", "failed"):
            return
        await self.supervisor.speak(notification.title)
