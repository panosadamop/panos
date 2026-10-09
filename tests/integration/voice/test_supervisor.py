"""Voice supervisor routing, notifications and the /voice API (worker replaced by a fake)."""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from panos_core.config.settings import VoiceSettings
from panos_core.domain.enums import JobStatus
from panos_core.events import EventBus
from panos_core.notifications import Notification
from panos_core.voice.supervisor import VoiceChannel, VoiceSupervisor

pytestmark = pytest.mark.req("BR-005", "FR-002")


@dataclass
class FakeJob:
    id: str
    status: JobStatus
    objective: str = "build the thing"


@dataclass
class FakeRunner:
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def pause(self, job_id: str, *, actor: str) -> None:
        self.calls.append(("pause", job_id))

    async def resume(self, job_id: str, *, actor: str) -> None:
        self.calls.append(("resume", job_id))

    async def cancel(self, job_id: str, *, actor: str) -> None:
        self.calls.append(("cancel", job_id))


class FakeJobs:
    def __init__(self, jobs: list[FakeJob]) -> None:
        self.jobs = jobs

    async def list(self, limit: int = 50) -> list[FakeJob]:
        return self.jobs


class FakeStdin:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def write(self, data: bytes) -> None:
        self.sent.append(json.loads(data))

    async def drain(self) -> None:
        return None


class FakeProc:
    returncode = None

    def __init__(self) -> None:
        self.stdin = FakeStdin()


def supervisor(
    tmp_path: Path, jobs: list[FakeJob] | None = None
) -> tuple[VoiceSupervisor, FakeProc, FakeRunner, list[str], EventBus]:
    asked: list[str] = []

    async def reply(text: str, conversation: str | None) -> tuple[str, str]:
        asked.append(text)
        return f"answer to {text}", "cnv_1"

    async def vocabulary() -> list[str]:
        return ["orders"]

    runner = FakeRunner()
    events = EventBus()
    sup = VoiceSupervisor(
        VoiceSettings(), tmp_path, tmp_path, events, reply, FakeJobs(jobs or []), runner, vocabulary
    )
    proc = FakeProc()
    sup._proc = proc  # type: ignore[assignment]
    sup.status.running = True
    return sup, proc, runner, asked, events


@pytest.mark.req("US-004", "AC-US004-01", "AC-US004-02", "AC-US004-03", "US-052", "AC-US052-01")
async def test_routing_speech_control_vs_job_control_vs_conversation(tmp_path: Path) -> None:
    jobs = [FakeJob("job_run", JobStatus.RUNNING), FakeJob("job_paused", JobStatus.PAUSED)]
    sup, proc, runner, asked, _ = supervisor(tmp_path, jobs)
    assert await sup.handle_transcript("Πάνο, σταμάτα να μιλάς") == "stopped talking"
    assert proc.stdin.sent[-1] == {"cmd": "stop_talking"}
    assert runner.calls == []  # stopping speech never touches jobs (AC-US004-02)
    assert (await sup.handle_transcript("pause")).startswith("Pausing")
    assert (await sup.handle_transcript("Panos resume")).startswith("Resuming")
    assert (await sup.handle_transcript("stop")).startswith("Cancelled")
    assert runner.calls == [("pause", "job_run"), ("resume", "job_paused"), ("cancel", "job_run")]
    answer = await sup.handle_transcript("Panos, what is my next meeting?")
    assert answer == "answer to what is my next meeting?" and asked == ["what is my next meeting?"]
    assert proc.stdin.sent[-1] == {"cmd": "speak", "text": answer}
    assert await sup.handle_transcript("never mind") == "ignored"
    assert await sup.handle_transcript("mute") == "muted" and sup.settings  # muted via config
    assert proc.stdin.sent[-1] == {"cmd": "configure", "settings": {"muted": True}}


async def test_job_control_without_jobs_explains(tmp_path: Path) -> None:
    sup, *_ = supervisor(tmp_path)
    assert await sup.handle_transcript("pause") == "No job is running."
    assert await sup.handle_transcript("resume") == "No job to resume."


@pytest.mark.req("US-051", "AC-US051-01", "AC-US051-02")
async def test_only_important_notifications_are_spoken(tmp_path: Path) -> None:
    sup, proc, *_ = supervisor(tmp_path)
    channel = VoiceChannel(sup)
    await channel.send(Notification(kind="approval", title="Job needs your approval: x", body=""))
    await channel.send(Notification(kind="completed", title="Job completed: y", body=""))
    assert [m["text"] for m in proc.stdin.sent] == [
        "Job needs your approval: x",
        "Job completed: y",
    ]
    sup.settings.voice_notifications = False
    await channel.send(Notification(kind="failed", title="Job failed", body=""))
    assert len(proc.stdin.sent) == 2


async def test_worker_events_update_status_and_reach_the_event_bus(tmp_path: Path) -> None:
    sup, _proc, _, asked, events = supervisor(tmp_path)
    async with events.subscribe() as queue:
        await sup._on_event({"event": "ready", "wake_word": "hey_jarvis_v0.1"})
        await sup._on_event({"event": "state", "state": "listening"})
        await sup._on_event({"event": "transcript", "text": "open orders"})
        await asyncio.sleep(0.05)
        types = [queue.get_nowait().type for _ in range(queue.qsize())]
    assert types[:3] == ["voice.ready", "voice.state", "voice.transcript"]
    assert "voice.reply" in types
    assert sup.status.wake_word == "hey_jarvis_v0.1" and sup.status.state == "listening"
    assert sup.status.last_transcript == "open orders" and asked == ["open orders"]


async def test_start_without_models_explains(tmp_path: Path) -> None:
    sup, *_ = supervisor(tmp_path)
    sup._proc = None
    status = await sup.start()
    assert not status.running and "panos voice setup" in (status.last_error or "")
    assert "whisper/small" in status.missing_models
    assert await sup.listen() is False  # nothing running: commands are no-ops


def test_voice_api(client: TestClient) -> None:
    status = client.get("/api/v1/voice/status").json()
    assert status["running"] is False and status["enabled"] is False
    started = client.post("/api/v1/voice/start").json()
    assert started["missing_models"] and "voice setup" in (started["last_error"] or "")
    assert client.post("/api/v1/voice/listen").json() == {"sent": False}
    changed = client.patch("/api/v1/voice/settings", json={"wake_word": True, "tts": False}).json()
    assert changed["wake_word_enabled"] is True and changed["tts"] is False
    result = client.post("/api/v1/voice/utterance", json={"text": "list projects"}).json()
    assert "No projects" in result["result"] or "Registered projects" in result["result"]
    assert client.post("/api/v1/voice/utterance", json={"text": "pause"}).json() == {
        "result": "No job is running."
    }
