"""Project-scoped filesystem tools (TOOLS.md "Filesystem", PERMISSIONS.md, ADR-024).

All paths go through :func:`resolve_in_root`. Writes into ``.git`` are classified as
``fs.write.git_internal`` (forbidden): editing Git internals would bypass Git governance.
Deletes are ``fs.delete`` (P2) only for a single clean, Git-tracked file; everything
else is ``fs.delete.unrecoverable`` (P3).
"""

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from panos_core.tools.contract import (
    ActionPlan,
    PlanRejectedError,
    SideEffect,
    Tool,
    ToolContext,
    ToolExecutionError,
    ToolSpec,
)
from panos_core.tools.gitinfo import is_tracked_and_clean
from panos_core.tools.paths import (
    PathPolicyError,
    is_git_internal,
    relative_to_root,
    resolve_in_root,
)

MAX_READ_BYTES: Final = 2_000_000
MAX_WRITE_BYTES: Final = 5_000_000
BULK_DELETE_THRESHOLD: Final = 20
SKIP_DIRS: Final = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "target",
        "dist",
        "build",
        ".idea",
        ".gradle",
    }
)
_PRIVACY: Final = "Reads/writes project files; content is subject to the project privacy mode."


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _root(ctx: ToolContext) -> Path:
    if ctx.project is None:
        raise PlanRejectedError("no active project")
    root = Path(ctx.project.path)
    if not root.is_dir():
        raise PlanRejectedError("project directory does not exist")
    return root.resolve()


def _resolve(root: Path, raw: str) -> Path:
    try:
        return resolve_in_root(root, raw)
    except PathPolicyError as exc:
        raise PlanRejectedError(str(exc)) from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ===================================================================== fs.list
class FsListInput(_Model):
    path: str = "."
    recursive: bool = False
    max_entries: int = Field(default=1000, ge=1, le=10_000)


class FsEntry(_Model):
    path: str
    type: Literal["file", "dir", "symlink", "other"]
    size: int | None = None


class FsListOutput(_Model):
    entries: list[FsEntry]
    truncated: bool


class FsListTool(Tool[FsListInput, FsListOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="fs.list",
        version="1",
        description="List files in a project directory.",
        input_model=FsListInput,
        output_model=FsListOutput,
        capabilities=frozenset({"fs.list"}),
        side_effect=SideEffect.NONE,
        timeout_s=60,
        cancellable=True,
        idempotent=True,
        privacy=_PRIVACY,
    )

    async def plan(self, inp: FsListInput, ctx: ToolContext) -> ActionPlan:
        root = _root(ctx)
        target = _resolve(root, inp.path)
        rel = relative_to_root(root, target)
        return ActionPlan(
            action="fs.list",
            target=str(root),
            summary=f"List {rel}",
            payload={"path": rel, "recursive": inp.recursive},
        )

    async def _execute(self, inp: FsListInput, ctx: ToolContext, plan: ActionPlan) -> FsListOutput:
        root = _root(ctx)
        base = _resolve(root, inp.path)
        if not base.is_dir():
            raise ToolExecutionError("not a directory")
        return await asyncio.to_thread(self._walk, root, base, inp, ctx)

    @staticmethod
    def _walk(root: Path, base: Path, inp: FsListInput, ctx: ToolContext) -> FsListOutput:
        entries: list[FsEntry] = []
        stack = [base]
        while stack:
            ctx.cancel.raise_if_cancelled()
            current = stack.pop()
            for child in sorted(current.iterdir(), key=lambda p: p.name.lower()):
                if len(entries) >= inp.max_entries:
                    return FsListOutput(entries=entries, truncated=True)
                rel = (
                    relative_to_root(root, child)
                    if not child.is_symlink()
                    else (Path(os.path.relpath(child, root)).as_posix())
                )
                if child.is_symlink():
                    entries.append(FsEntry(path=rel, type="symlink"))
                elif child.is_dir():
                    entries.append(FsEntry(path=rel, type="dir"))
                    if inp.recursive and child.name not in SKIP_DIRS:
                        stack.append(child)
                elif child.is_file():
                    entries.append(FsEntry(path=rel, type="file", size=child.stat().st_size))
                else:
                    entries.append(FsEntry(path=rel, type="other"))
        return FsListOutput(entries=entries, truncated=False)


# ===================================================================== fs.read
class FsReadInput(_Model):
    path: str
    max_bytes: int = Field(default=1_000_000, ge=1, le=MAX_READ_BYTES)


class FsReadOutput(_Model):
    path: str
    content: str
    size: int
    sha256: str
    truncated: bool
    binary: bool


class FsReadTool(Tool[FsReadInput, FsReadOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="fs.read",
        version="1",
        description="Read a text file inside the project.",
        input_model=FsReadInput,
        output_model=FsReadOutput,
        capabilities=frozenset({"fs.read"}),
        side_effect=SideEffect.NONE,
        timeout_s=30,
        cancellable=False,
        idempotent=True,
        privacy=_PRIVACY,
    )

    async def plan(self, inp: FsReadInput, ctx: ToolContext) -> ActionPlan:
        root = _root(ctx)
        rel = relative_to_root(root, _resolve(root, inp.path))
        return ActionPlan(
            action="fs.read", target=str(root), summary=f"Read {rel}", payload={"path": rel}
        )

    async def _execute(self, inp: FsReadInput, ctx: ToolContext, plan: ActionPlan) -> FsReadOutput:
        root = _root(ctx)
        path = _resolve(root, inp.path)
        if not path.is_file():
            raise ToolExecutionError("not a file")
        data = await asyncio.to_thread(path.read_bytes)
        binary = b"\x00" in data[:8192]
        shown = data[: inp.max_bytes]
        return FsReadOutput(
            path=relative_to_root(root, path),
            content="" if binary else shown.decode("utf-8", errors="replace"),
            size=len(data),
            sha256=_sha256(data),
            truncated=len(data) > inp.max_bytes,
            binary=binary,
        )


# ===================================================================== fs.search
class FsSearchInput(_Model):
    query: str = Field(min_length=1, max_length=500)
    path: str = "."
    regex: bool = False
    case_sensitive: bool = False
    max_results: int = Field(default=200, ge=1, le=5000)


class FsMatch(_Model):
    path: str
    line: int
    text: str


class FsSearchOutput(_Model):
    matches: list[FsMatch]
    truncated: bool


class FsSearchTool(Tool[FsSearchInput, FsSearchOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="fs.search",
        version="1",
        description="Search text in project files.",
        input_model=FsSearchInput,
        output_model=FsSearchOutput,
        capabilities=frozenset({"fs.search"}),
        side_effect=SideEffect.NONE,
        timeout_s=120,
        cancellable=True,
        idempotent=True,
        privacy=_PRIVACY,
    )

    async def plan(self, inp: FsSearchInput, ctx: ToolContext) -> ActionPlan:
        root = _root(ctx)
        rel = relative_to_root(root, _resolve(root, inp.path))
        if inp.regex:
            try:
                re.compile(inp.query)
            except re.error as exc:
                raise PlanRejectedError(f"invalid regex: {exc}") from exc
        return ActionPlan(
            action="fs.search",
            target=str(root),
            summary=f"Search {inp.query!r} in {rel}",
            payload={"path": rel, "query": inp.query, "regex": inp.regex},
        )

    async def _execute(
        self, inp: FsSearchInput, ctx: ToolContext, plan: ActionPlan
    ) -> FsSearchOutput:
        root = _root(ctx)
        base = _resolve(root, inp.path)
        return await asyncio.to_thread(self._search, root, base, inp, ctx)

    @staticmethod
    def _search(root: Path, base: Path, inp: FsSearchInput, ctx: ToolContext) -> FsSearchOutput:
        flags = 0 if inp.case_sensitive else re.IGNORECASE
        pattern = re.compile(inp.query if inp.regex else re.escape(inp.query), flags)
        matches: list[FsMatch] = []
        files = [base] if base.is_file() else []
        if base.is_dir():
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                files.extend(Path(dirpath) / f for f in filenames)
        for file in files:
            ctx.cancel.raise_if_cancelled()
            try:
                if file.is_symlink() or file.stat().st_size > MAX_READ_BYTES:
                    continue
                data = file.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:8192]:
                continue
            for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                if pattern.search(line):
                    matches.append(
                        FsMatch(path=relative_to_root(root, file), line=number, text=line[:500])
                    )
                    if len(matches) >= inp.max_results:
                        return FsSearchOutput(matches=matches, truncated=True)
        return FsSearchOutput(matches=matches, truncated=False)


# ===================================================================== fs.write
class FsWriteInput(_Model):
    path: str
    content: str
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    create_only: bool = False


class FsWriteOutput(_Model):
    path: str
    bytes_written: int
    sha256: str
    created: bool


class FsWriteTool(Tool[FsWriteInput, FsWriteOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="fs.write",
        version="1",
        description="Create or replace a file inside the project (atomic write).",
        input_model=FsWriteInput,
        output_model=FsWriteOutput,
        capabilities=frozenset({"fs.write", "fs.write.git_internal"}),
        side_effect=SideEffect.LOCAL,
        timeout_s=30,
        cancellable=False,
        idempotent=True,
        privacy=_PRIVACY,
    )

    async def plan(self, inp: FsWriteInput, ctx: ToolContext) -> ActionPlan:
        root = _root(ctx)
        path = _resolve(root, inp.path)
        data = inp.content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise PlanRejectedError(f"content exceeds {MAX_WRITE_BYTES} bytes")
        if path == root or path.is_dir():
            raise PlanRejectedError("target is a directory")
        rel = relative_to_root(root, path)
        action = "fs.write.git_internal" if is_git_internal(root, path) else "fs.write"
        verb = "Update" if path.exists() else "Create"
        return ActionPlan(
            action=action,
            target=str(root),
            summary=f"{verb} {rel} ({len(data)} bytes)",
            payload={
                "path": rel,
                "content_sha256": _sha256(data),
                "expected_sha256": inp.expected_sha256,
                "create_only": inp.create_only,
            },
        )

    async def _execute(
        self, inp: FsWriteInput, ctx: ToolContext, plan: ActionPlan
    ) -> FsWriteOutput:
        root = _root(ctx)
        path = _resolve(root, inp.path)
        data = inp.content.encode("utf-8")
        existed = path.exists()
        if existed and inp.create_only:
            raise ToolExecutionError("file already exists")
        if inp.expected_sha256 is not None:
            current = _sha256(path.read_bytes()) if existed else None
            if current != inp.expected_sha256:
                raise ToolExecutionError("file changed since it was read (sha256 mismatch)")
        await asyncio.to_thread(_atomic_write, path, data)
        return FsWriteOutput(
            path=relative_to_root(root, path),
            bytes_written=len(data),
            sha256=_sha256(data),
            created=not existed,
        )


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".panos-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ===================================================================== fs.move
class FsMoveInput(_Model):
    source: str
    destination: str
    overwrite: bool = False


class FsMoveOutput(_Model):
    source: str
    destination: str


class FsMoveTool(Tool[FsMoveInput, FsMoveOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="fs.move",
        version="1",
        description="Move or rename a file inside the project.",
        input_model=FsMoveInput,
        output_model=FsMoveOutput,
        capabilities=frozenset({"fs.move", "fs.write.git_internal", "fs.delete.unrecoverable"}),
        side_effect=SideEffect.LOCAL,
        timeout_s=60,
        cancellable=False,
        idempotent=False,
        privacy=_PRIVACY,
    )

    async def plan(self, inp: FsMoveInput, ctx: ToolContext) -> ActionPlan:
        root = _root(ctx)
        src, dst = _resolve(root, inp.source), _resolve(root, inp.destination)
        if src == root:
            raise PlanRejectedError("cannot move the project root")
        src_rel, dst_rel = relative_to_root(root, src), relative_to_root(root, dst)
        if is_git_internal(root, src) or is_git_internal(root, dst):
            action = "fs.write.git_internal"
        elif dst.exists() and inp.overwrite:
            action = "fs.delete.unrecoverable"  # overwriting destroys the destination
        else:
            action = "fs.move"
        return ActionPlan(
            action=action,
            target=str(root),
            summary=f"Move {src_rel} → {dst_rel}",
            payload={"source": src_rel, "destination": dst_rel, "overwrite": inp.overwrite},
        )

    async def _execute(self, inp: FsMoveInput, ctx: ToolContext, plan: ActionPlan) -> FsMoveOutput:
        root = _root(ctx)
        src, dst = _resolve(root, inp.source), _resolve(root, inp.destination)
        if not src.exists():
            raise ToolExecutionError("source does not exist")
        if dst.exists() and not inp.overwrite:
            raise ToolExecutionError("destination exists")
        dst.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.move, str(src), str(dst))
        return FsMoveOutput(
            source=relative_to_root(root, src), destination=relative_to_root(root, dst)
        )


# ===================================================================== fs.delete
class FsDeleteInput(_Model):
    path: str
    recursive: bool = False


class FsDeleteOutput(_Model):
    path: str
    files_deleted: int


class FsDeleteTool(Tool[FsDeleteInput, FsDeleteOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="fs.delete",
        version="1",
        description="Delete a file or directory in the project.",
        input_model=FsDeleteInput,
        output_model=FsDeleteOutput,
        capabilities=frozenset({"fs.delete", "fs.delete.unrecoverable", "fs.write.git_internal"}),
        side_effect=SideEffect.LOCAL,
        timeout_s=120,
        cancellable=False,
        idempotent=True,
        privacy=_PRIVACY,
    )

    async def plan(self, inp: FsDeleteInput, ctx: ToolContext) -> ActionPlan:
        root = _root(ctx)
        path = _resolve(root, inp.path)
        if path == root:
            raise PlanRejectedError("cannot delete the project root")
        if not path.exists() and not path.is_symlink():
            raise PlanRejectedError("path does not exist")
        rel = relative_to_root(root, path)
        facts: dict[str, object] = {}
        if is_git_internal(root, path):
            action = "fs.write.git_internal"
        elif path.is_dir() and not path.is_symlink():
            count = sum(len(files) for _, _, files in os.walk(path))
            facts = {"directory": True, "file_count": count}
            action = "fs.delete.unrecoverable"
        else:
            recoverable = await is_tracked_and_clean(root, rel)
            facts = {"git_tracked_and_clean": recoverable}
            action = "fs.delete" if recoverable else "fs.delete.unrecoverable"
        return ActionPlan(
            action=action,
            target=str(root),
            summary=f"Delete {rel}",
            payload={"path": rel, "recursive": inp.recursive},
            facts=facts,
        )

    async def _execute(
        self, inp: FsDeleteInput, ctx: ToolContext, plan: ActionPlan
    ) -> FsDeleteOutput:
        root = _root(ctx)
        path = _resolve(root, inp.path)
        if path.is_dir() and not path.is_symlink():
            if not inp.recursive:
                raise ToolExecutionError("directory delete requires recursive=true")
            count = sum(len(files) for _, _, files in os.walk(path))
            await asyncio.to_thread(shutil.rmtree, path)
            return FsDeleteOutput(path=relative_to_root(root, path), files_deleted=count)
        rel = relative_to_root(root, path)
        await asyncio.to_thread(path.unlink)
        return FsDeleteOutput(path=rel, files_deleted=1)


FS_TOOLS: Final = (FsListTool, FsReadTool, FsSearchTool, FsWriteTool, FsMoveTool, FsDeleteTool)
