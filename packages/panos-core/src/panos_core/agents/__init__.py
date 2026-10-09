"""Agent roles and the structured agent contract (AGENTS.md, ADR-010)."""

from panos_core.agents.contract import (
    AgentError,
    AgentOutcome,
    AgentRole,
    AgentTask,
    Handoff,
    extract_json,
    run_agent,
)
from panos_core.agents.roles import (
    DEVELOPER,
    PLANNER,
    QA,
    REVIEWER,
    ChangeSet,
    Diagnosis,
    FileEdit,
    Finding,
    Plan,
    PlanStep,
    Review,
)

__all__ = [
    "DEVELOPER",
    "PLANNER",
    "QA",
    "REVIEWER",
    "AgentError",
    "AgentOutcome",
    "AgentRole",
    "AgentTask",
    "ChangeSet",
    "Diagnosis",
    "FileEdit",
    "Finding",
    "Handoff",
    "Plan",
    "PlanStep",
    "Review",
    "extract_json",
    "run_agent",
]
