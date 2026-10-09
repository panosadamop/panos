"""Repository analysis service: builds and persists a RepositorySnapshot (FR-005).

Snapshots are taken before material modification (AC-US012-02). When critical structure
cannot be understood, the snapshot lists clarifications and a low confidence, so the
coding workflow (M5) asks instead of modifying blindly (AC-US012-03).
"""

import asyncio
from pathlib import Path
from typing import Any

from panos_core.audit import AuditService
from panos_core.domain import Project, RepositorySnapshot
from panos_core.observability import get_logger
from panos_core.persistence.unit_of_work import UnitOfWorkFactory
from panos_core.repository import gitcli
from panos_core.repository.detectors import COMMAND_KINDS, Detection, detect
from panos_core.repository.structure import (
    conventions,
    documentation,
    outline,
    python_packages,
    tree_summary,
)
from panos_core.repository.walk import WalkedFile, iter_project_files

log = get_logger(__name__)

CONFIDENCE_THRESHOLD = 0.6


def _scan(root: Path) -> tuple[list[WalkedFile], Detection, dict[str, Any]]:
    files = list(iter_project_files(root))
    detection = detect(root, files)
    structure = {
        "tree": tree_summary(files),
        "python_packages": python_packages(files),
        "outline": outline(files),
        "conventions": conventions(files),
        "documentation": documentation(files),
    }
    return files, detection, structure


async def _git_facts(root: Path) -> dict[str, Any]:
    if not await gitcli.is_repository(root):
        return {"is_repository": False}
    try:
        status = await gitcli.status(root)
        commits = await gitcli.log(root, limit=10)
        remotes = await gitcli.remotes(root)
    except gitcli.GitError as exc:
        return {"is_repository": True, "error": str(exc)[:300]}
    return {
        "is_repository": True,
        "branch": status.branch,
        "head": status.head,
        "upstream": status.upstream,
        "ahead": status.ahead,
        "behind": status.behind,
        "changed_files": sum(1 for e in status.entries if e.kind != "untracked"),
        "untracked_files": sum(1 for e in status.entries if e.kind == "untracked"),
        "recent_commits": [
            {"short": c.short, "date": c.date, "subject": c.subject[:200]} for c in commits
        ],
        "contributors_recent": len({c.author for c in commits}),
        "remotes": remotes,
    }


def assess(
    det: Detection, structure: dict[str, Any], git: dict[str, Any]
) -> tuple[float, list[str]]:
    """Return (confidence, clarifications). Clarifications are questions for the user."""
    confidence = 1.0
    questions: list[str] = []
    if not det.manifests:
        confidence -= 0.5
        questions.append(
            "No recognised build manifest at the repository root. "
            "Which build tool and entry point does this project use?"
        )
    if not det.commands.get("test"):
        confidence -= 0.2
        questions.append("No test command detected. How are tests run for this project?")
    primary_build = det.build_systems - {"node"} if len(det.build_systems) > 1 else set()
    if len(primary_build) > 1:
        confidence -= 0.15
        questions.append(
            f"Several build systems at the root ({', '.join(sorted(primary_build))}"
            "). Which one is authoritative?"
        )
    if len(det.nested_manifests) >= 2 and not det.workspaces:
        confidence -= 0.1
        questions.append(
            f"Looks like a monorepo ({len(det.nested_manifests)} nested manifests). "
            "Which packages should Panos work in, and how are they built/tested?"
        )
    if structure["tree"]["total_files"] == 0:
        confidence = 0.0
        questions.append("The project directory has no analysable files.")
    if git.get("error"):
        confidence -= 0.1
    return max(0.0, round(confidence, 2)), questions


class AnalysisService:
    def __init__(self, uow_factory: UnitOfWorkFactory, audit: AuditService) -> None:
        self._uow_factory = uow_factory
        self._audit = audit

    async def analyze(self, project: Project, *, actor: str = "user") -> RepositorySnapshot:
        root = Path(project.path)
        if not root.is_dir():
            raise FileNotFoundError(f"project directory missing: {root}")
        _files, det, structure = await asyncio.to_thread(_scan, root)
        git = await _git_facts(root)
        structure["git"] = git
        confidence, clarifications = assess(det, structure, git)
        snapshot = RepositorySnapshot(
            project_id=project.id,
            commit_hash=git.get("head"),
            stack=det.as_stack(),
            commands={k: det.commands[k] for k in COMMAND_KINDS if k in det.commands},
            structure=structure,
            clarifications=clarifications,
            confidence=confidence,
        )
        async with self._uow_factory() as uow:
            await uow.snapshots.add(snapshot)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="project.analyzed",
            action="repository.analyze",
            project_id=project.id,
            metadata={
                "snapshot_id": snapshot.id,
                "confidence": confidence,
                "frameworks": snapshot.stack.get("frameworks", []),
                "clarifications": len(clarifications),
                "commit": snapshot.commit_hash,
            },
        )
        log.info("project_analyzed", project_id=project.id, confidence=confidence)
        return snapshot

    async def latest(self, project_id: str) -> RepositorySnapshot | None:
        async with self._uow_factory() as uow:
            return await uow.snapshots.latest(project_id)
