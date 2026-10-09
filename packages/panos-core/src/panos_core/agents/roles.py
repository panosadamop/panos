"""Internal specialist roles for the coding workflow (AGENTS.md): Planner, Developer,
Reviewer and QA. Each returns a validated structured result."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from panos_core.agents.contract import AgentRole
from panos_core.ai.types import Complexity, TaskType


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PlanStep(_Out):
    description: str
    files: list[str] = Field(default_factory=list)


class Plan(_Out):
    summary: str
    steps: list[PlanStep] = Field(min_length=1)
    files_to_read: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class FileEdit(_Out):
    path: str
    action: Literal["create", "replace", "edit", "delete"]
    content: str | None = None  # create / replace
    search: str | None = None  # edit: exact text that occurs once
    replace: str | None = None  # edit: replacement text


class ChangeSet(_Out):
    summary: str
    edits: list[FileEdit] = Field(default_factory=list)
    commit_message: str = ""
    decisions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class Finding(_Out):
    severity: Literal["info", "minor", "major", "critical"] = "minor"
    path: str = ""
    message: str


class Review(_Out):
    verdict: Literal["approve", "changes_requested"]
    summary: str
    findings: list[Finding] = Field(default_factory=list)


class Diagnosis(_Out):
    summary: str
    likely_cause: str = ""
    files: list[str] = Field(default_factory=list)


PLANNER = AgentRole(
    name="Planner",
    output_model=Plan,
    task=TaskType.CODING,
    complexity=Complexity.MEDIUM,
    instructions=(
        "Turn the objective into a short, concrete implementation plan for this repository. "
        "Follow the repository's existing conventions and stack. List the files to read and "
        "change (paths relative to the project root). Keep the plan minimal; record "
        "assumptions and open questions instead of guessing silently."
    ),
)

DEVELOPER = AgentRole(
    name="Developer",
    output_model=ChangeSet,
    task=TaskType.CODING,
    complexity=Complexity.MEDIUM,
    max_output_tokens=8192,
    instructions=(
        "Implement the plan as file edits. Use action 'edit' with an exact 'search' snippet "
        "that occurs exactly once in the file and its 'replace' text for small changes; "
        "'create' for new files and 'replace' for full rewrites (give the whole file in "
        "'content'); 'delete' only when the plan requires it. Paths are relative to the "
        "project root. Add or update tests for the change. Provide a one-line "
        "conventional commit message."
    ),
)

REVIEWER = AgentRole(
    name="Reviewer",
    output_model=Review,
    task=TaskType.REVIEW,
    complexity=Complexity.MEDIUM,
    instructions=(
        "Review the diff independently for correctness, security, maintainability, "
        "architecture fit and requirement coverage. Approve only if it is correct and "
        "complete; otherwise request changes with specific findings."
    ),
)

QA = AgentRole(
    name="QA",
    output_model=Diagnosis,
    task=TaskType.CODING,
    complexity=Complexity.MEDIUM,
    instructions=(
        "Diagnose the failing format/lint/type-check/build/test output. Identify the most "
        "likely cause and the files that need to change. Do not propose unrelated changes."
    ),
)
