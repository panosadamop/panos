import itertools

import pytest

from panos_core.domain import JobStatus
from panos_core.jobs import TRANSITIONS, InvalidTransitionError, can_transition, ensure_transition

S = JobStatus
pytestmark = pytest.mark.req("FR-022")


def test_every_state_has_a_transition_entry() -> None:
    assert set(TRANSITIONS) == set(JobStatus)


def test_terminal_states_have_no_exits() -> None:
    for state in JobStatus:
        assert (not TRANSITIONS[state]) == state.is_terminal


@pytest.mark.req("US-052", "AC-US052-01")
def test_every_non_terminal_state_can_be_cancelled_and_paused() -> None:
    for state in JobStatus:
        if state.is_terminal:
            continue
        assert can_transition(state, S.CANCELLED)
        assert state == S.PAUSED or can_transition(state, S.PAUSED)


def test_completion_only_from_running() -> None:
    assert [s for s in JobStatus if can_transition(s, S.COMPLETED)] == [S.RUNNING]


@pytest.mark.req("AC-US041-02")
def test_approval_wait_reachable_from_active_states_and_resumable() -> None:
    for state in (S.PLANNING, S.RUNNING, S.PAUSED):
        assert can_transition(state, S.WAITING_FOR_APPROVAL)
    assert can_transition(S.WAITING_FOR_APPROVAL, S.RUNNING)
    assert can_transition(S.WAITING_FOR_APPROVAL, S.BLOCKED)  # rejected


def test_no_self_transitions() -> None:
    for state in JobStatus:
        assert not can_transition(state, state)


def test_exhaustive_ensure_transition_matches_table() -> None:
    for a, b in itertools.product(JobStatus, repeat=2):
        if b in TRANSITIONS[a]:
            ensure_transition(a, b)
        else:
            with pytest.raises(InvalidTransitionError):
                ensure_transition(a, b)


def test_every_state_reachable_from_queued() -> None:
    seen, frontier = {S.QUEUED}, [S.QUEUED]
    while frontier:
        for nxt in TRANSITIONS[frontier.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(JobStatus)
