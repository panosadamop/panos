from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from panos_core.domain import (
    ActionApproval,
    AuditEvent,
    Job,
    JobBudget,
    JobStatus,
    JobStep,
    Node,
    PermissionLevel,
    PolicyDecision,
    PrivacyMode,
    Project,
    new_id,
)

NODE_ID = new_id("node")
SHA = "a" * 64


@pytest.mark.req("FR-022")
def test_job_status_has_exactly_the_fr022_states() -> None:
    assert [s.value for s in JobStatus] == [
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
    ]


@pytest.mark.req("FR-022")
def test_terminal_job_states() -> None:
    assert {s for s in JobStatus if s.is_terminal} == {
        JobStatus.FAILED,
        JobStatus.COMPLETED,
        JobStatus.CANCELLED,
    }


def test_permission_levels_are_ordered_by_restrictiveness() -> None:
    assert PermissionLevel.P0_READ < PermissionLevel.P3_APPROVAL_REQUIRED
    assert max(PermissionLevel) is PermissionLevel.P4_FORBIDDEN


@pytest.mark.req("FR-004", "BR-004", "AC-US005-01")
def test_new_project_defaults_to_local_only() -> None:
    project = Project(name="demo", path="C:/work/demo", node_id=NODE_ID)
    assert project.privacy_mode is PrivacyMode.LOCAL_ONLY


@pytest.mark.req("FR-004")
@pytest.mark.parametrize(
    "path", ["C:\\work\\demo", "C:/work/demo", "/home/u/demo", "\\\\server\\share\\demo"]
)
def test_project_accepts_absolute_paths(path: str) -> None:
    assert Project(name="demo", path=path, node_id=NODE_ID).path == path


@pytest.mark.req("FR-004")
@pytest.mark.parametrize("path", ["demo", "./demo", "..\\demo", "C:demo"])
def test_project_rejects_relative_paths(path: str) -> None:
    with pytest.raises(ValidationError, match="absolute"):
        Project(name="demo", path=path, node_id=NODE_ID)


def test_entity_ids_are_type_checked() -> None:
    with pytest.raises(ValidationError):
        Project(name="demo", path="/x", node_id=new_id("job"))


def test_entities_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Node(name="n", platform="windows", colour="blue")  # type: ignore[call-arg]


def test_assignment_is_validated() -> None:
    job = Job(node_id=NODE_ID, objective="do it")
    with pytest.raises(ValidationError):
        job.status = "NOT_A_STATE"  # type: ignore[assignment]


def test_naive_datetimes_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Job(node_id=NODE_ID, objective="x", created_at=datetime(2026, 1, 1))  # noqa: DTZ001


def test_job_completion_cannot_precede_start() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError, match="precede"):
        Job(
            node_id=NODE_ID,
            objective="x",
            started_at=start,
            completed_at=start - timedelta(seconds=1),
        )


@pytest.mark.req("FR-023", "AC-US050-02")
def test_job_budget_supports_runtime_cost_token_and_repair_limits() -> None:
    budget = JobBudget(
        max_runtime_s=3600, max_cost_usd=2.5, max_tokens=100_000, max_repair_attempts=3
    )
    assert budget.model_dump() == {
        "max_runtime_s": 3600,
        "max_cost_usd": 2.5,
        "max_tokens": 100_000,
        "max_repair_attempts": 3,
    }
    with pytest.raises(ValidationError):
        JobBudget(max_runtime_s=0)
    with pytest.raises(ValidationError):
        JobBudget(max_cost_usd=-1)


def test_step_sequence_starts_at_one() -> None:
    with pytest.raises(ValidationError):
        JobStep(job_id=new_id("job"), sequence=0)


@pytest.mark.req("FR-010")
def test_approval_requires_sha256_payload_hash_and_future_expiry() -> None:
    now = datetime.now(UTC)
    approval = ActionApproval(
        action_type="git.commit",
        target="repo",
        payload_hash=SHA,
        summary="commit 3 files",
        requested_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    assert approval.status == "PENDING"
    with pytest.raises(ValidationError):
        ActionApproval(
            action_type="git.commit",
            target="repo",
            payload_hash="not-a-hash",
            summary="s",
            expires_at=now + timedelta(minutes=10),
        )
    with pytest.raises(ValidationError, match="expires_at"):
        ActionApproval(
            action_type="git.commit",
            target="repo",
            payload_hash=SHA,
            summary="s",
            requested_at=now,
            expires_at=now,
        )


@pytest.mark.req("FR-028")
def test_audit_event_captures_actor_action_decision_and_metadata() -> None:
    event = AuditEvent(
        node_id=NODE_ID,
        actor="user",
        event_type="policy.decision",
        action="git.commit",
        policy_decision=PolicyDecision.REQUIRE_APPROVAL,
        metadata={"reason": "P3"},
    )
    assert event.created_at.tzinfo is not None
    assert event.policy_decision is PolicyDecision.REQUIRE_APPROVAL
