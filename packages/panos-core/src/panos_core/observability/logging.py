"""Logging configuration.

All log records (structlog and stdlib, e.g. uvicorn and SQLAlchemy) pass through the
same processor chain: context IDs → level/timestamp → secret redaction → JSON (file)
and JSON or console rendering (stderr).
"""

import logging
import logging.handlers
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import structlog
from structlog.typing import EventDict, Processor

from panos_core.redaction import redact

LOG_FILE_NAME = "panos.log"
_MAX_BYTES = 10 * 1024 * 1024
_BACKUPS = 5


def redact_processor(_logger: Any, _method: str, event_dict: MutableMapping[str, Any]) -> EventDict:
    """structlog processor that redacts secrets in every event field."""
    return dict(redact(dict(event_dict)))


_SHARED_PROCESSORS: list[Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    redact_processor,
]


def configure_logging(
    *, level: str = "INFO", log_dir: Path | None = None, json_console: bool = False
) -> None:
    """Configure structlog and route stdlib logging through it. Safe to call repeatedly."""
    handlers: list[logging.Handler] = []

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=_SHARED_PROCESSORS,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer()
                if json_console
                else structlog.dev.ConsoleRenderer(colors=False),
            ],
        )
    )
    handlers.append(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / LOG_FILE_NAME, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
        )
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                foreign_pre_chain=_SHARED_PROCESSORS,
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.JSONRenderer(),
                ],
            )
        )
        handlers.append(file_handler)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
        existing.close()
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)

    structlog.configure(
        processors=[*_SHARED_PROCESSORS, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
