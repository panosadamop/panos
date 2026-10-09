"""Table-driven job state machine (FR-022, ADR-010/011).

Terminal states (FAILED, COMPLETED, CANCELLED) have no outgoing transitions.
Every non-terminal state can be cancelled. ``COMPLETED`` is reachable only from
``RUNNING``, so a job cannot finish while waiting for a tool, approval or the user.
"""

from collections.abc import Mapping
from typing import Final

from panos_core.domain.enums import JobStatus as S
from panos_core.domain.errors import DomainError

TRANSITIONS: Final[Mapping[S, frozenset[S]]] = {
    S.QUEUED: frozenset({S.PLANNING, S.RUNNING, S.PAUSED, S.CANCELLED}),
    S.PLANNING: frozenset(
        {
            S.RUNNING,
            S.WAITING_FOR_USER,
            S.WAITING_FOR_APPROVAL,
            S.PAUSED,
            S.BLOCKED,
            S.FAILED,
            S.CANCELLED,
        }
    ),
    S.RUNNING: frozenset(
        {
            S.WAITING_FOR_TOOL,
            S.WAITING_FOR_APPROVAL,
            S.WAITING_FOR_USER,
            S.PAUSED,
            S.BLOCKED,
            S.FAILED,
            S.COMPLETED,
            S.CANCELLED,
        }
    ),
    S.WAITING_FOR_TOOL: frozenset({S.RUNNING, S.PAUSED, S.BLOCKED, S.FAILED, S.CANCELLED}),
    # Approval granted → RUNNING; rejected → BLOCKED (or the job is cancelled).
    S.WAITING_FOR_APPROVAL: frozenset({S.RUNNING, S.PAUSED, S.BLOCKED, S.FAILED, S.CANCELLED}),
    S.WAITING_FOR_USER: frozenset({S.RUNNING, S.PLANNING, S.PAUSED, S.CANCELLED}),
    # Resume continues where the job was (ADR-011: interrupted jobs recover to PAUSED).
    S.PAUSED: frozenset(
        {S.RUNNING, S.PLANNING, S.WAITING_FOR_APPROVAL, S.WAITING_FOR_USER, S.CANCELLED}
    ),
    S.BLOCKED: frozenset({S.RUNNING, S.PLANNING, S.PAUSED, S.FAILED, S.CANCELLED}),
    S.FAILED: frozenset(),
    S.COMPLETED: frozenset(),
    S.CANCELLED: frozenset(),
}


class InvalidTransitionError(DomainError):
    def __init__(self, current: S, target: S) -> None:
        super().__init__(f"invalid job transition {current} -> {target}")
        self.current = current
        self.target = target


def can_transition(current: S, target: S) -> bool:
    return target in TRANSITIONS[current]


def ensure_transition(current: S, target: S) -> None:
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)
