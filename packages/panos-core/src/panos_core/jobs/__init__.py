"""Jobs: state machine and lifecycle service (FR-022, ADR-011)."""

from panos_core.jobs.service import JobService
from panos_core.jobs.state_machine import (
    TRANSITIONS,
    InvalidTransitionError,
    can_transition,
    ensure_transition,
)

__all__ = [
    "TRANSITIONS",
    "InvalidTransitionError",
    "JobService",
    "can_transition",
    "ensure_transition",
]
