"""Checkpoints in a per-project shadow Git repository (ADR-012, FR-024, US-023).

The shadow repository lives in ``<data>/checkpoints/<project_id>.git`` and is driven with
``GIT_DIR``/``GIT_WORK_TREE``, so the project's own ``.git`` (history, index, stash) is
never read or written. It works the same for projects that are not Git repositories.

* Files ignored by the project's ``.gitignore`` files are not checkpointed (reported).
* ``info/attributes`` forces ``-text`` so checkout restores the exact bytes regardless of
  the project's or the user's line-ending settings.
* Rollback first records the current state (so a rollback can itself be undone), then
  deletes files created after the checkpoint and restores every checkpointed file.
"""

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path

from panos_core.audit import AuditService
from panos_core.config import PanosSettings
from panos_core.domain import Project
from panos_core.domain.errors import DomainError
from panos_core.domain.models import Checkpoint
from panos_core.persistence.unit_of_work import UnitOfWorkFactory
from panos_core.repository.gitcli import GitError, run_git

# Never checkpoint the user's Git data or regenerable caches (ADR-012 "Panos excludes").
SHADOW_EXCLUDES = "\n".join(
    [
        ".git/",
        "__pycache__/",
        "*.pyc",
        ".pytest_cache/",
        ".mypy_cache/",
        ".ruff_cache/",
        ".tox/",
        ".gradle/",
        ".idea/",
        ".vs/",
        "",
    ]
)
_IDENTITY = (
    "-c",
    "user.name=Panos checkpoint",
    "-c",
    "user.email=checkpoint@panos.local",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.safecrlf=false",
)


class CheckpointError(DomainError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class FileChange:
    status: str  # A(dded) | M(odified) | D(eleted) | R(enamed)
    path: str


@dataclass(frozen=True)
class RollbackReport:
    checkpoint_id: str
    restored: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    safety_reference: str = ""  # shadow commit of the state just before the rollback
    verified: bool = False


class CheckpointService:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, audit: AuditService, settings: PanosSettings
    ) -> None:
        self._uow_factory = uow_factory
        self._audit = audit
        self._root = settings.data_dir / "checkpoints"
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ plumbing
    def shadow_dir(self, project_id: str) -> Path:
        return self._root / f"{project_id}.git"

    def _lock(self, project_id: str) -> asyncio.Lock:
        return self._locks.setdefault(project_id, asyncio.Lock())

    async def _git(self, project: Project, *args: str) -> str:
        env = {
            "GIT_DIR": str(self.shadow_dir(project.id)),
            "GIT_WORK_TREE": project.path,
            "GIT_CEILING_DIRECTORIES": str(Path(project.path).parent),
        }
        return await run_git(Path(project.path), *_IDENTITY, *args, timeout_s=300, env_extra=env)

    async def _ensure(self, project: Project) -> None:
        shadow = self.shadow_dir(project.id)
        if (shadow / "HEAD").exists():
            return
        shadow.parent.mkdir(parents=True, exist_ok=True)
        await run_git(shadow.parent, "init", "-q", "--bare", str(shadow))
        await self._git(project, "config", "core.bare", "false")
        info = shadow / "info"
        info.mkdir(exist_ok=True)
        (info / "exclude").write_text(SHADOW_EXCLUDES, encoding="utf-8")
        (info / "attributes").write_text("* -text\n", encoding="utf-8")

    async def _snapshot(self, project: Project, message: str) -> str:
        await self._git(project, "add", "-A", "--", ".")
        await self._git(project, "commit", "-q", "--no-verify", "--allow-empty", "-m", message)
        return (await self._git(project, "rev-parse", "HEAD")).strip()

    # ------------------------------------------------------------------ API
    async def create(
        self, project: Project, *, job_id: str | None, actor: str, label: str = ""
    ) -> Checkpoint:
        if not Path(project.path).is_dir():
            raise CheckpointError("not_found", f"project directory {project.path} is missing")
        async with self._lock(project.id):
            await self._ensure(project)
            sha = await self._snapshot(project, f"checkpoint {job_id or ''} {label}".strip())
        checkpoint = Checkpoint(project_id=project.id, job_id=job_id, reference=sha)
        async with self._uow_factory() as uow:
            await uow.checkpoints.add(checkpoint)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="checkpoint.created",
            action="checkpoint.create",
            project_id=project.id,
            job_id=job_id,
            metadata={"checkpoint_id": checkpoint.id, "reference": sha, "label": label},
        )
        return checkpoint

    async def get(self, checkpoint_id: str) -> Checkpoint:
        async with self._uow_factory() as uow:
            checkpoint = await uow.checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise CheckpointError("not_found", f"checkpoint {checkpoint_id!r} not found")
        return checkpoint

    async def for_job(self, job_id: str) -> list[Checkpoint]:
        async with self._uow_factory() as uow:
            return list(await uow.checkpoints.list_for_job(job_id))

    async def changes(self, project: Project, checkpoint: Checkpoint) -> list[FileChange]:
        """Files changed in the project since ``checkpoint`` (excluding ignored files)."""
        async with self._lock(project.id):
            await self._git(project, "add", "-A", "--", ".")
            out = await self._git(
                project,
                "diff",
                "--cached",
                "--name-status",
                "-z",
                "--no-renames",
                checkpoint.reference,
            )
        parts = [p for p in out.split("\0") if p]
        return [FileChange(parts[i][0], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]

    async def diff(
        self, project: Project, checkpoint: Checkpoint, *, max_chars: int = 20_000
    ) -> str:
        async with self._lock(project.id):
            await self._git(project, "add", "-A", "--", ".")
            text = await self._git(project, "diff", "--cached", "--no-color", checkpoint.reference)
        return text if len(text) <= max_chars else text[:max_chars] + "\n... (diff truncated)"

    async def rollback(
        self, project: Project, checkpoint: Checkpoint, *, actor: str
    ) -> RollbackReport:
        if checkpoint.project_id != project.id:
            raise CheckpointError("mismatch", "checkpoint belongs to another project")
        root = Path(project.path).resolve()
        async with self._lock(project.id):
            await self._ensure(project)
            try:
                safety = await self._snapshot(project, f"before rollback to {checkpoint.id}")
                changed = await self._git(
                    project,
                    "diff",
                    "--name-status",
                    "-z",
                    "--no-renames",
                    checkpoint.reference,
                    safety,
                )
            except GitError as exc:
                raise CheckpointError("git_failed", str(exc)) from exc
            parts = [p for p in changed.split("\0") if p]
            pairs = [(parts[i][0], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]
            deleted: list[str] = []
            for status, rel in pairs:
                if status != "A":
                    continue
                target = (root / rel).resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    continue
                target.unlink()
                deleted.append(rel)
                _prune_empty_dirs(target.parent, root)
            restored = [rel for status, rel in pairs if status != "A"]
            if restored:
                await self._git(project, "checkout", "-f", checkpoint.reference, "--", ".")
            await self._git(project, "add", "-A", "--", ".")
            residual = await self._git(
                project, "diff", "--cached", "--name-only", checkpoint.reference
            )
        report = RollbackReport(
            checkpoint_id=checkpoint.id,
            restored=restored,
            deleted=deleted,
            safety_reference=safety,
            verified=not residual.strip(),
        )
        await self._audit.record(
            actor=actor,
            event_type="checkpoint.rollback",
            action="checkpoint.rollback",
            project_id=project.id,
            job_id=checkpoint.job_id,
            metadata={
                "checkpoint_id": checkpoint.id,
                "restored": len(restored),
                "deleted": len(deleted),
                "safety_reference": safety,
                "verified": report.verified,
            },
        )
        return report


def _prune_empty_dirs(directory: Path, root: Path) -> None:
    while directory != root and directory.is_relative_to(root):
        try:
            if any(directory.iterdir()):
                return
            os.rmdir(directory)
        except OSError:
            return
        directory = directory.parent
