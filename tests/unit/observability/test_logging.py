import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from panos_core.observability import (
    bind_context,
    clear_context,
    configure_logging,
    get_correlation_id,
    get_logger,
)
from panos_core.observability.context import sanitize_correlation_id
from panos_core.observability.logging import LOG_FILE_NAME


@pytest.fixture
def log_dir(tmp_path: Path) -> Iterator[Path]:
    directory = tmp_path / "logs"
    configure_logging(level="DEBUG", log_dir=directory)
    yield directory
    clear_context()
    configure_logging(level="WARNING")


def _records(log_dir: Path) -> list[dict[str, object]]:
    for handler in logging.getLogger().handlers:
        handler.flush()
    lines = (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.mark.req("NFR-OBS-001")
def test_log_events_carry_correlation_job_and_step_ids(log_dir: Path) -> None:
    bind_context(correlation_id="corr-12345678", job_id="job_1", step_id="stp_1")
    get_logger("test").info("step_started", tool="fs.read")
    record = _records(log_dir)[-1]
    assert record["event"] == "step_started"
    assert record["correlation_id"] == "corr-12345678"
    assert record["job_id"] == "job_1"
    assert record["step_id"] == "stp_1"
    assert record["level"] == "info"
    assert str(record["timestamp"]).endswith("Z")


@pytest.mark.req("NFR-SEC-004")
def test_secrets_are_redacted_in_log_file(log_dir: Path) -> None:
    get_logger("test").info(
        "calling provider", api_key="sk-ant-" + "x" * 30, detail="Bearer abcdefghijklmnop"
    )
    raw = (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "sk-ant-" not in raw
    assert "abcdefghijklmnop" not in raw
    assert "[REDACTED]" in raw


@pytest.mark.req("NFR-SEC-004", "NFR-OBS-001")
def test_stdlib_logs_are_structured_and_redacted(log_dir: Path) -> None:
    logging.getLogger("uvicorn.error").warning("token=supersecretvalue in url")
    record = _records(log_dir)[-1]
    assert record["logger"] == "uvicorn.error"
    assert "supersecretvalue" not in json.dumps(record)


def test_unknown_context_key_rejected() -> None:
    with pytest.raises(ValueError, match="unknown context keys"):
        bind_context(user="x")


def test_correlation_id_accessor_and_clear() -> None:
    bind_context(correlation_id="abcdefgh1234")
    assert get_correlation_id() == "abcdefgh1234"
    clear_context()
    assert get_correlation_id() is None


@pytest.mark.parametrize(
    ("value", "kept"),
    [
        ("abcd-1234-EFGH", True),
        ("short", False),
        ("bad id with spaces", False),
        (None, False),
        ("x" * 65, False),
    ],
)
def test_sanitize_correlation_id(value: str | None, kept: bool) -> None:
    result = sanitize_correlation_id(value)
    assert (result == value) is kept
    assert 8 <= len(result) <= 64
