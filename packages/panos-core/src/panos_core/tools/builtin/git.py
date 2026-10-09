"""Typed Git tools (FR-013, BR-017).

Read tools are ``git.read`` (P0). Branch creation is ``git.mutate.local`` (P1). Commits,
pushes and other history changes are *not* exposed here: they go through the shell
classifier and approval flow (M5 adds a dedicated commit-approval tool).

Option injection is prevented: revisions are validated (no leading ``-``) and paths are
always passed after ``--``, so ``--output=...`` style arguments cannot reach Git.
"""

import re
from pathlib import Path
from typing import Annotated, ClassVar, Final

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from panos_core.repository import gitcli
from panos_core.tools.contract import (
    ActionPlan,
    PlanRejectedError,
    SideEffect,
    Tool,
    ToolContext,
    ToolExecutionError,
    ToolSpec,
)
from panos_core.tools.paths import PathPolicyError, relative_to_root, resolve_in_root

_REV_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-~^@{}:]{0,199}$")
_BRANCH_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,199}$")
_MAX_TEXT: Final = 500_000
_PRIVACY: Final = "Reads repository history and diffs; subject to project privacy mode."


def _check_rev(value: str) -> str:
    """Revisions may not start with '-' (option injection) and use a conservative charset."""
    if not _REV_RE.match(value):
        raise ValueError(f"invalid revision {value!r}")
    return value


Revision = Annotated[str, AfterValidator(_check_rev)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _repo_root(ctx: ToolContext) -> Path:
    if ctx.project is None:
        raise PlanRejectedError("no active project")
    root = Path(ctx.project.path).resolve()
    if not await gitcli.is_repository(root):
        raise PlanRejectedError("project is not a Git repository")
    return root


def _rel_path(root: Path, raw: str | None) -> str | None:
    if raw is None:
        return None
    try:
        return relative_to_root(root, resolve_in_root(root, raw))
    except PathPolicyError as exc:
        raise PlanRejectedError(str(exc)) from exc


def _truncate(text: str) -> tuple[str, bool]:
    return (text[:_MAX_TEXT], True) if len(text) > _MAX_TEXT else (text, False)


async def _git(root: Path, ctx: ToolContext, *args: str) -> str:
    try:
        return await gitcli.run_git(root, *args, cancel=ctx.cancel)
    except gitcli.GitError as exc:
        raise ToolExecutionError(str(exc)) from exc


def _read_spec(name: str, description: str, inp: type[BaseModel], out: type[BaseModel]) -> ToolSpec:
    return ToolSpec(
        name=name,
        version="1",
        description=description,
        input_model=inp,
        output_model=out,
        capabilities=frozenset({"git.read"}),
        side_effect=SideEffect.NONE,
        timeout_s=120,
        cancellable=True,
        idempotent=True,
        privacy=_PRIVACY,
    )


# ===================================================================== git.status
class GitStatusInput(_Model):
    include_untracked: bool = True


class GitStatusEntryOut(_Model):
    path: str
    index: str
    worktree: str
    kind: str
    orig_path: str | None = None


class GitStatusOutput(_Model):
    branch: str | None
    head: str | None
    upstream: str | None
    ahead: int
    behind: int
    dirty: bool
    entries: list[GitStatusEntryOut]


class GitStatusTool(Tool[GitStatusInput, GitStatusOutput]):
    spec: ClassVar[ToolSpec] = _read_spec(
        "git.status", "Working tree status.", GitStatusInput, GitStatusOutput
    )

    async def plan(self, inp: GitStatusInput, ctx: ToolContext) -> ActionPlan:
        root = await _repo_root(ctx)
        return ActionPlan(action="git.read", target=str(root), summary="git status", payload={})

    async def _execute(
        self, inp: GitStatusInput, ctx: ToolContext, plan: ActionPlan
    ) -> GitStatusOutput:
        root = Path(plan.target)
        status = gitcli.parse_status_v2(
            await _git(
                root,
                ctx,
                "status",
                "--porcelain=v2",
                "--branch",
                "-z",
                "--untracked-files=" + ("normal" if inp.include_untracked else "no"),
            )
        )
        return GitStatusOutput(
            branch=status.branch,
            head=status.head,
            upstream=status.upstream,
            ahead=status.ahead,
            behind=status.behind,
            dirty=status.dirty,
            entries=[
                GitStatusEntryOut(
                    path=e.path,
                    index=e.index,
                    worktree=e.worktree,
                    kind=e.kind,
                    orig_path=e.orig_path,
                )
                for e in status.entries
            ],
        )


# ===================================================================== git.diff
class GitDiffInput(_Model):
    staged: bool = False
    base: Revision | None = None  # revision to diff against (default: index/HEAD)
    path: str | None = None
    stat_only: bool = False


class GitDiffFile(_Model):
    path: str
    added: int | None
    deleted: int | None


class GitDiffOutput(_Model):
    files: list[GitDiffFile]
    diff: str
    truncated: bool


class GitDiffTool(Tool[GitDiffInput, GitDiffOutput]):
    spec: ClassVar[ToolSpec] = _read_spec(
        "git.diff", "Diff of working tree, index or a base revision.", GitDiffInput, GitDiffOutput
    )

    async def plan(self, inp: GitDiffInput, ctx: ToolContext) -> ActionPlan:
        root = await _repo_root(ctx)
        rel = _rel_path(root, inp.path)
        return ActionPlan(
            action="git.read",
            target=str(root),
            summary="git diff",
            payload={"staged": inp.staged, "base": inp.base, "path": rel},
        )

    async def _execute(
        self, inp: GitDiffInput, ctx: ToolContext, plan: ActionPlan
    ) -> GitDiffOutput:
        root = Path(plan.target)
        args = ["diff", "--no-color", "--no-ext-diff"]
        if inp.staged:
            args.append("--cached")
        if inp.base:
            args.append(inp.base)
        tail = ["--", plan.payload["path"]] if plan.payload["path"] else ["--"]
        numstat = await _git(root, ctx, *args, "--numstat", *tail)
        files = []
        for line in numstat.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                files.append(
                    GitDiffFile(
                        path=parts[2],
                        added=int(parts[0]) if parts[0].isdigit() else None,
                        deleted=int(parts[1]) if parts[1].isdigit() else None,
                    )
                )
        text, truncated = (
            ("", False) if inp.stat_only else _truncate(await _git(root, ctx, *args, *tail))
        )
        return GitDiffOutput(files=files, diff=text, truncated=truncated)


# ===================================================================== git.log
class GitLogInput(_Model):
    limit: int = Field(default=20, ge=1, le=500)
    path: str | None = None
    rev: Revision | None = None


class GitCommitOut(_Model):
    hash: str
    short: str
    author: str
    date: str
    subject: str


class GitLogOutput(_Model):
    commits: list[GitCommitOut]


class GitLogTool(Tool[GitLogInput, GitLogOutput]):
    spec: ClassVar[ToolSpec] = _read_spec("git.log", "Commit history.", GitLogInput, GitLogOutput)

    async def plan(self, inp: GitLogInput, ctx: ToolContext) -> ActionPlan:
        root = await _repo_root(ctx)
        return ActionPlan(
            action="git.read",
            target=str(root),
            summary="git log",
            payload={"limit": inp.limit, "rev": inp.rev, "path": _rel_path(root, inp.path)},
        )

    async def _execute(self, inp: GitLogInput, ctx: ToolContext, plan: ActionPlan) -> GitLogOutput:
        root = Path(plan.target)
        args = ["log", f"--max-count={inp.limit}", "--format=%H%x1f%h%x1f%an%x1f%aI%x1f%s%x1e"]
        if inp.rev:
            args.append(inp.rev)
        args.append("--")
        if plan.payload["path"]:
            args.append(plan.payload["path"])
        try:
            text = await gitcli.run_git(root, *args, cancel=ctx.cancel)
        except gitcli.GitError as exc:
            if "does not have any commits" in str(exc):
                return GitLogOutput(commits=[])
            raise ToolExecutionError(str(exc)) from exc
        return GitLogOutput(commits=[GitCommitOut(**c.__dict__) for c in gitcli.parse_log(text)])


# ===================================================================== git.branches
class GitBranchesInput(_Model):
    pass


class GitBranchOut(_Model):
    name: str
    commit: str
    current: bool


class GitBranchesOutput(_Model):
    branches: list[GitBranchOut]


class GitBranchesTool(Tool[GitBranchesInput, GitBranchesOutput]):
    spec: ClassVar[ToolSpec] = _read_spec(
        "git.branches", "Local and remote branches.", GitBranchesInput, GitBranchesOutput
    )

    async def plan(self, inp: GitBranchesInput, ctx: ToolContext) -> ActionPlan:
        root = await _repo_root(ctx)
        return ActionPlan(action="git.read", target=str(root), summary="git branches", payload={})

    async def _execute(
        self, inp: GitBranchesInput, ctx: ToolContext, plan: ActionPlan
    ) -> GitBranchesOutput:
        try:
            raw = await gitcli.branches(Path(plan.target))
        except gitcli.GitError as exc:
            raise ToolExecutionError(str(exc)) from exc
        return GitBranchesOutput(
            branches=[
                GitBranchOut(
                    name=str(b["name"]), commit=str(b["commit"]), current=bool(b["current"])
                )
                for b in raw
            ]
        )


# ===================================================================== git.show / git.blame
class GitShowInput(_Model):
    rev: Revision = "HEAD"
    path: str | None = None
    stat_only: bool = False


class GitTextOutput(_Model):
    text: str
    truncated: bool


class GitShowTool(Tool[GitShowInput, GitTextOutput]):
    spec: ClassVar[ToolSpec] = _read_spec(
        "git.show", "Show a commit (or a file at a revision).", GitShowInput, GitTextOutput
    )

    async def plan(self, inp: GitShowInput, ctx: ToolContext) -> ActionPlan:
        root = await _repo_root(ctx)
        return ActionPlan(
            action="git.read",
            target=str(root),
            summary=f"git show {inp.rev}",
            payload={"rev": inp.rev, "path": _rel_path(root, inp.path)},
        )

    async def _execute(
        self, inp: GitShowInput, ctx: ToolContext, plan: ActionPlan
    ) -> GitTextOutput:
        root = Path(plan.target)
        path = plan.payload["path"]
        if path:
            text = await _git(root, ctx, "show", "--no-color", f"{inp.rev}:{path}")
        else:
            args = ["show", "--no-color", "--no-ext-diff"]
            if inp.stat_only:
                args.append("--stat")
            text = await _git(root, ctx, *args, inp.rev, "--")
        out, truncated = _truncate(text)
        return GitTextOutput(text=out, truncated=truncated)


class GitBlameInput(_Model):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class GitBlameTool(Tool[GitBlameInput, GitTextOutput]):
    spec: ClassVar[ToolSpec] = _read_spec(
        "git.blame", "Line-by-line authorship of a file.", GitBlameInput, GitTextOutput
    )

    async def plan(self, inp: GitBlameInput, ctx: ToolContext) -> ActionPlan:
        root = await _repo_root(ctx)
        return ActionPlan(
            action="git.read",
            target=str(root),
            summary=f"git blame {inp.path}",
            payload={"path": _rel_path(root, inp.path)},
        )

    async def _execute(
        self, inp: GitBlameInput, ctx: ToolContext, plan: ActionPlan
    ) -> GitTextOutput:
        args = ["blame", "--date=short"]
        if inp.end_line is not None:
            args.append(f"-L{inp.start_line},{inp.end_line}")
        text = await _git(Path(plan.target), ctx, *args, "--", plan.payload["path"])
        out, truncated = _truncate(text)
        return GitTextOutput(text=out, truncated=truncated)


# ===================================================================== git.create_branch
class GitCreateBranchInput(_Model):
    name: str = Field(min_length=1, max_length=200)
    start_point: Revision | None = None
    switch: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _BRANCH_RE.match(v) or ".." in v or v.endswith((".lock", "/", ".")) or "//" in v:
            raise ValueError(f"invalid branch name {v!r}")
        return v


class GitCreateBranchOutput(_Model):
    name: str
    switched: bool


class GitCreateBranchTool(Tool[GitCreateBranchInput, GitCreateBranchOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="git.create_branch",
        version="1",
        description="Create a local branch (optionally switch to it).",
        input_model=GitCreateBranchInput,
        output_model=GitCreateBranchOutput,
        capabilities=frozenset({"git.mutate.local"}),
        side_effect=SideEffect.LOCAL,
        timeout_s=60,
        cancellable=False,
        idempotent=False,
        privacy=_PRIVACY,
    )

    async def plan(self, inp: GitCreateBranchInput, ctx: ToolContext) -> ActionPlan:
        root = await _repo_root(ctx)
        return ActionPlan(
            action="git.mutate.local",
            target=str(root),
            summary=f"Create branch {inp.name}",
            payload={"name": inp.name, "start_point": inp.start_point, "switch": inp.switch},
        )

    async def _execute(
        self, inp: GitCreateBranchInput, ctx: ToolContext, plan: ActionPlan
    ) -> GitCreateBranchOutput:
        root = Path(plan.target)
        await _git(root, ctx, "check-ref-format", "--branch", inp.name)
        start = [inp.start_point] if inp.start_point else []
        if inp.switch:
            await _git(root, ctx, "switch", "-c", inp.name, *start)
        else:
            await _git(root, ctx, "branch", inp.name, *start)
        return GitCreateBranchOutput(name=inp.name, switched=inp.switch)


GIT_TOOLS: Final = (
    GitStatusTool,
    GitDiffTool,
    GitLogTool,
    GitBranchesTool,
    GitShowTool,
    GitBlameTool,
    GitCreateBranchTool,
)
