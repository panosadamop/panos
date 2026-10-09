"""Command classification (ADR-007).

``classify_argv`` maps one command (argv) to one or more findings, each an action class
from the policy (``git.commit``, ``docker.exec``, ``shell.unknown``...). Nested
interpreters (``bash -c``, ``pwsh -Command``, ``cmd /c``, ``wsl``) are parsed and their
inner commands classified recursively. The caller takes the most restrictive finding.

Design rules:
* unknown executables and anything static analysis cannot see through → ``shell.unknown``;
* classification is by *behaviour*, so a Git commit is ``git.commit`` whether requested
  through a Git tool or typed into a shell (no approval bypass via an alternative tool);
* only explicitly recognised read-only forms are P0.
"""

import base64
import binascii
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePath
from typing import Final

from panos_core.shell.bash import parse_bash
from panos_core.shell.cmd import parse_cmd
from panos_core.shell.parsed import DYNAMIC, ParsedScript
from panos_core.shell.powershell import parse_powershell

MAX_DEPTH: Final = 4


@dataclass(frozen=True)
class Finding:
    action: str
    reason: str
    command: tuple[str, ...]


def normalize_exe(name: str) -> str:
    base = PurePath(name.replace("\\", "/")).name.lower()
    for ext in (".exe", ".cmd", ".bat", ".com", ".ps1", ".sh"):
        if base.endswith(ext):
            return base[: -len(ext)]
    return base


def _f(action: str, reason: str, argv: Sequence[str]) -> list[Finding]:
    return [Finding(action, reason, tuple(argv))]


def _unknown(reason: str, argv: Sequence[str]) -> list[Finding]:
    return _f("shell.unknown", reason, argv)


def _positionals(args: Sequence[str]) -> list[str]:
    return [a for a in args if not a.startswith("-")]


# ===================================================================== git
_GIT_READ: Final = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "blame",
        "rev-parse",
        "ls-files",
        "ls-tree",
        "describe",
        "shortlog",
        "grep",
        "cat-file",
        "rev-list",
        "name-rev",
        "merge-base",
        "version",
        "help",
        "count-objects",
        "check-ignore",
        "whatchanged",
        "show-ref",
        "for-each-ref",
        "var",
    }
)
_GIT_COMMIT: Final = frozenset({"commit", "cherry-pick", "revert", "am"})
_GIT_SHARED: Final = frozenset(
    {"push", "merge", "rebase", "pull", "filter-branch", "filter-repo", "replace", "submodule"}
)
_GIT_DESTRUCTIVE: Final = frozenset({"clean", "update-ref", "symbolic-ref", "prune", "gc"})
_GIT_LOCAL: Final = frozenset(
    {
        "add",
        "mv",
        "switch",
        "init",
        "fetch",
        "sparse-checkout",
        "notes",
        "worktree",
        "apply",
        "format-patch",
    }
)
_GIT_SAFE_GLOBAL_FLAGS: Final = frozenset(
    {"--no-pager", "-P", "--no-optional-locks", "--literal-pathspecs"}
)


def _classify_git(argv: Sequence[str]) -> list[Finding]:
    rest = list(argv[1:])
    while rest and rest[0].startswith("-"):
        flag = rest.pop(0)
        if flag not in _GIT_SAFE_GLOBAL_FLAGS:
            # -c (config/alias injection), -C/--git-dir/--work-tree (escape the project)...
            return _unknown(f"git global option {flag!r}", argv)
    if not rest:
        return _f("git.read", "git without subcommand", argv)
    sub, args = rest[0], rest[1:]
    if sub == DYNAMIC:
        return _unknown("dynamic git subcommand", argv)
    if sub in _GIT_READ:
        return _f("git.read", f"git {sub}", argv)
    if sub in _GIT_COMMIT:
        return _f("git.commit", f"git {sub} creates commits", argv)
    if sub in _GIT_SHARED:
        return _f("git.mutate.shared", f"git {sub} changes shared history", argv)
    if sub in _GIT_DESTRUCTIVE:
        if sub == "clean" and any(a in ("-n", "--dry-run") for a in args):
            return _f("git.read", "git clean --dry-run", argv)
        return _f("git.destructive", f"git {sub}", argv)
    if sub == "branch":
        if any(
            a in ("-D", "-d", "--delete", "-m", "-M", "--move", "-f", "--force", "-c", "-C")
            for a in args
        ):
            return _f("git.destructive", "git branch delete/rename/force", argv)
        listing = {
            "-a",
            "--all",
            "-r",
            "--remotes",
            "-l",
            "--list",
            "-v",
            "-vv",
            "--show-current",
            "--merged",
            "--no-merged",
            "--contains",
        }
        if not args or all(a in listing or a.startswith("--format") for a in args):
            return _f("git.read", "git branch (list)", argv)
        return _f("git.mutate.local", "git branch (create)", argv)
    if sub == "tag":
        if not args or all(a in ("-l", "--list", "-n") for a in args):
            return _f("git.read", "git tag (list)", argv)
        return _f("git.mutate.shared", "git tag creates/changes a release tag", argv)
    if sub == "stash":
        action = args[0] if args else "push"
        if action in ("list", "show"):
            return _f("git.read", f"git stash {action}", argv)
        if action in ("drop", "clear"):
            return _f("git.destructive", f"git stash {action}", argv)
        return _f("git.mutate.local", f"git stash {action}", argv)
    if sub == "remote":
        if not args or args[0] in ("-v", "--verbose", "show", "get-url"):
            return _f("git.read", "git remote (read)", argv)
        return _f("git.mutate.local", "git remote change", argv)
    if sub == "config":
        if any(
            a in ("--get", "--get-all", "--list", "-l", "--show-origin", "--get-regexp")
            for a in args
        ):
            return _f("git.read", "git config (read)", argv)
        return _unknown("git config write (hooks/aliases can execute code)", argv)
    if sub == "reset":
        if any(a in ("--hard", "--merge", "--keep") for a in args):
            return _f("git.destructive", "git reset --hard", argv)
        return _f("git.mutate.local", "git reset (soft/mixed)", argv)
    if sub in ("checkout", "restore"):
        if sub == "restore" and "--staged" in args and "--worktree" not in args:
            return _f("git.mutate.local", "git restore --staged", argv)
        if sub == "restore" or any(
            a in ("--", ".", "-f", "--force", "-p", "--patch") for a in args
        ):
            return _f("git.destructive", f"git {sub} discards working-tree changes", argv)
        return _f("git.mutate.local", f"git {sub} (switch branch)", argv)
    if sub == "rm":
        if "--cached" in args:
            return _f("git.mutate.local", "git rm --cached", argv)
        return _f("git.destructive", "git rm deletes files", argv)
    if sub in _GIT_LOCAL:
        return _f("git.mutate.local", f"git {sub}", argv)
    return _unknown(f"unrecognised git subcommand {sub!r}", argv)


# ===================================================================== containers
_DOCKER_READ: Final = frozenset(
    {
        "ps",
        "images",
        "inspect",
        "version",
        "info",
        "logs",
        "top",
        "history",
        "diff",
        "port",
        "events",
    }
)
_DOCKER_MGMT: Final = frozenset(
    {
        "image",
        "container",
        "network",
        "volume",
        "context",
        "system",
        "builder",
        "plugin",
        "node",
        "service",
        "stack",
        "secret",
        "config",
    }
)
_DOCKER_MGMT_READ: Final = frozenset({"ls", "list", "inspect", "df", "history"})
_COMPOSE_READ: Final = frozenset({"config", "ps", "ls", "images", "version", "top", "logs", "port"})
_COMPOSE_VALUE_OPTS: Final = frozenset(
    {
        "-f",
        "--file",
        "-p",
        "--project-name",
        "--profile",
        "--env-file",
        "--project-directory",
        "--ansi",
    }
)


def _classify_compose(argv: Sequence[str], args: list[str]) -> list[Finding]:
    while args and args[0].startswith("-"):
        opt = args.pop(0)
        if opt in _COMPOSE_VALUE_OPTS and args:
            args.pop(0)
    sub = args[0] if args else ""
    if sub in _COMPOSE_READ:
        return _f("docker.inspect", f"compose {sub}", argv)
    return _f("docker.exec", f"compose {sub or '(none)'} executes", argv)


def _classify_docker(argv: Sequence[str]) -> list[Finding]:
    exe = normalize_exe(argv[0])
    args = list(argv[1:])
    if exe == "docker-compose":
        return _classify_compose(argv, args)
    while args and args[0].startswith("-"):
        opt = args.pop(0)
        if opt in ("-H", "--host", "--tlsverify", "--tlscacert"):
            return _f("docker.exec", f"docker {opt} targets another daemon", argv)
        if opt in ("--context", "-c", "--config", "-l", "--log-level") and args:
            args.pop(0)
    if not args:
        return _f("docker.inspect", "docker without subcommand", argv)
    sub = args[0]
    if sub == "compose":
        return _classify_compose(argv, args[1:])
    if sub in _DOCKER_READ:
        return _f("docker.inspect", f"docker {sub}", argv)
    if sub in _DOCKER_MGMT and len(args) > 1 and args[1] in _DOCKER_MGMT_READ:
        return _f("docker.inspect", f"docker {sub} {args[1]}", argv)
    return _f("docker.exec", f"docker {sub} executes", argv)


_K8S_READ: Final = frozenset(
    {
        "get",
        "describe",
        "logs",
        "version",
        "explain",
        "api-resources",
        "api-versions",
        "cluster-info",
        "top",
        "diff",
        "events",
        "auth",
    }
)
_K8S_VALUE_OPTS: Final = frozenset(
    {
        "-n",
        "--namespace",
        "--context",
        "--kubeconfig",
        "--cluster",
        "--user",
        "-o",
        "--output",
        "-l",
        "--selector",
    }
)
_HELM_READ: Final = frozenset(
    {
        "list",
        "ls",
        "status",
        "get",
        "show",
        "template",
        "version",
        "history",
        "search",
        "lint",
        "env",
    }
)


def _classify_k8s(argv: Sequence[str]) -> list[Finding]:
    exe = normalize_exe(argv[0])
    args = list(argv[1:])
    positional: list[str] = []
    while args:
        a = args.pop(0)
        if a in _K8S_VALUE_OPTS and args:
            args.pop(0)
        elif not a.startswith("-"):
            positional.append(a)
    sub = positional[0] if positional else ""
    if exe == "helm":
        if sub in _HELM_READ or (sub in ("repo", "dependency") and positional[1:2] == ["list"]):
            return _f("k8s.read", f"helm {sub}", argv)
        return _f("k8s.mutate", f"helm {sub or '(none)'} changes the cluster", argv)
    if sub in _K8S_READ:
        return _f("k8s.read", f"{exe} {sub}", argv)
    if (
        sub == "config"
        and positional[1:2]
        and positional[1]
        in ("view", "get-contexts", "current-context", "get-clusters", "get-users")
    ):
        return _f("k8s.read", f"{exe} config {positional[1]}", argv)
    return _f("k8s.mutate", f"{exe} {sub or '(none)'} changes the cluster", argv)


# ===================================================================== packages / builds
_PKG_INSTALL_SUBS: Final = {
    "pip": {"install", "uninstall", "download"},
    "pip3": {"install", "uninstall", "download"},
    "uv": {"add", "remove", "sync", "lock", "pip", "tool"},
    "poetry": {"add", "install", "update", "remove", "lock"},
    "pipx": {"install", "upgrade", "inject"},
    "npm": {"install", "i", "ci", "add", "update", "up", "uninstall", "remove", "rm", "link"},
    "pnpm": {"install", "i", "add", "update", "up", "remove", "rm", "link"},
    "yarn": {"install", "add", "upgrade", "remove", "link"},
    "bun": {"install", "i", "add", "remove", "update"},
    "composer": {"install", "require", "update", "remove"},
    "go": {"get", "install"},
    "cargo": {"add", "install", "remove"},
    "gem": {"install", "uninstall"},
    "bundle": {"install", "update", "add"},
    "dotnet": {"add", "restore"},
    "choco": {"install", "upgrade", "uninstall"},
    "winget": {"install", "upgrade", "uninstall"},
    "scoop": {"install", "update", "uninstall"},
    "brew": {"install", "upgrade", "uninstall", "reinstall"},
    "apt": {"install", "remove", "upgrade", "purge"},
    "apt-get": {"install", "remove", "upgrade", "purge"},
    "dnf": {"install", "remove", "upgrade"},
    "yum": {"install", "remove", "update"},
    "pacman": {"-S", "-R", "-U", "-Syu"},
    "snap": {"install", "remove"},
}
_PKG_READ_SUBS: Final = {
    "list",
    "show",
    "freeze",
    "check",
    "outdated",
    "ls",
    "view",
    "info",
    "search",
    "why",
    "tree",
    "--version",
    "-v",
    "version",
    "audit",
}
_SCRIPT_TEST_NAMES: Final = frozenset(
    {
        "test",
        "tests",
        "lint",
        "build",
        "format",
        "fmt",
        "typecheck",
        "type-check",
        "check",
        "tsc",
        "coverage",
    }
)
_TEST_TOOLS: Final = frozenset(
    {
        "pytest",
        "py.test",
        "mypy",
        "ruff",
        "black",
        "isort",
        "flake8",
        "pylint",
        "pyright",
        "tox",
        "nox",
        "eslint",
        "prettier",
        "tsc",
        "jest",
        "vitest",
        "mocha",
        "phpunit",
        "rspec",
        "golangci-lint",
        "ktlint",
        "gofmt",
        "rustfmt",
        "stylelint",
        "biome",
        "pre-commit",
    }
)
_BUILD_TOOLS: Final = {
    "mvn": (
        {
            "test",
            "verify",
            "package",
            "compile",
            "clean",
            "install",
            "validate",
            "test-compile",
            "spotless:check",
            "spotless:apply",
            "checkstyle:check",
        },
        {"deploy"},
    ),
    "mvnw": (
        {"test", "verify", "package", "compile", "clean", "install", "validate", "test-compile"},
        {"deploy"},
    ),
    "gradle": (
        {
            "test",
            "build",
            "check",
            "assemble",
            "clean",
            "compilejava",
            "lint",
            "spotlesscheck",
            "spotlessapply",
        },
        {"publish"},
    ),
    "gradlew": (
        {
            "test",
            "build",
            "check",
            "assemble",
            "clean",
            "compilejava",
            "lint",
            "spotlesscheck",
            "spotlessapply",
        },
        {"publish"},
    ),
    "go": ({"test", "vet", "build", "fmt"}, {"run", "generate"}),
    "cargo": ({"test", "build", "check", "clippy", "fmt", "bench", "doc"}, {"run", "publish"}),
    "dotnet": ({"test", "build", "format", "clean"}, {"run", "publish", "nuget"}),
    "make": (set(), set()),
}
_SINGLE_VERB_TOOLS: Final = frozenset({"go", "cargo", "dotnet"})
_PYTHON_SAFE_MODULES: Final = frozenset(
    {
        "pytest",
        "mypy",
        "ruff",
        "black",
        "isort",
        "flake8",
        "pylint",
        "unittest",
        "pyright",
        "coverage",
        "tox",
    }
)


def _classify_package_or_build(exe: str, argv: Sequence[str]) -> list[Finding] | None:
    args = list(argv[1:])
    pos = _positionals(args)
    sub = pos[0] if pos else (args[0] if args else "")

    if exe in ("npm", "pnpm", "yarn", "bun"):
        if sub in ("test", "t") or (sub == "run" and pos[1:2] and pos[1] in _SCRIPT_TEST_NAMES):
            return _f("build.test", f"{exe} test/lint script", argv)
        if exe in ("pnpm", "yarn") and sub in _SCRIPT_TEST_NAMES:
            return _f("build.test", f"{exe} {sub}", argv)
        if sub in ("run", "exec", "x", "dlx", "start", "create", "init"):
            return _unknown(f"{exe} {sub} runs arbitrary package code", argv)
        if exe == "yarn" and not args:
            return _f("pkg.install", "yarn (install)", argv)
    if exe in ("npx", "bunx", "pnpx"):
        return _unknown(f"{exe} downloads and runs package code", argv)

    if exe == "uv" and sub == "run":
        return None  # caller classifies the wrapped command
    if exe == "poetry" and sub == "run":
        return None
    if exe == "uv" and sub == "pip" and pos[1:2] and pos[1] in _PKG_READ_SUBS:
        return _f("shell.read", "uv pip (read)", argv)

    installs = _PKG_INSTALL_SUBS.get(exe)
    if installs is not None:
        if sub in installs or (args and args[0] in installs):
            return _f("pkg.install", f"{exe} {sub} installs packages", argv)
        if sub in _PKG_READ_SUBS or (args and args[0] in _PKG_READ_SUBS):
            return _f("shell.read", f"{exe} {sub}", argv)

    build = _BUILD_TOOLS.get(exe)
    if build is not None:
        safe, unsafe = build
        if exe in _SINGLE_VERB_TOOLS:
            # go/cargo/dotnet: only the first positional is the verb; the rest are
            # packages, patterns or test filters (e.g. `go test ./...`).
            goals = {pos[0].lower()} if pos else set()
        else:
            goals = {p.lower() for p in pos}
        if goals & unsafe:
            return _unknown(f"{exe} {sorted(goals & unsafe)} publishes/deploys/runs code", argv)
        if exe == "make":
            return _unknown("make runs arbitrary recipes (allowlist per project)", argv)
        if goals and goals <= safe:
            return _f("build.test", f"{exe} {' '.join(sorted(goals))}", argv)
        return _unknown(f"{exe} goals {sorted(goals)} not recognised", argv)
    return None


# ===================================================================== network / files
_UPLOAD_FLAGS: Final = frozenset(
    {
        "-T",
        "--upload-file",
        "-d",
        "--data",
        "--data-binary",
        "--data-raw",
        "--data-urlencode",
        "-F",
        "--form",
        "--json",
        "--post-data",
        "--post-file",
        "--body-data",
        "--body-file",
        "-InFile",
        "-Body",
    }
)
_MUTATING_METHODS: Final = frozenset({"post", "put", "patch", "delete"})
_FETCH_TOOLS: Final = frozenset(
    {"curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod", "irm", "http", "https", "xh"}
)
_TRANSFER_TOOLS: Final = frozenset({"scp", "sftp", "ftp", "rsync", "rclone", "azcopy", "gsutil"})
_REMOTE_EXEC: Final = frozenset(
    {
        "ssh",
        "telnet",
        "nc",
        "ncat",
        "netcat",
        "socat",
        "psexec",
        "invoke-command",
        "enter-pssession",
        "new-pssession",
    }
)


def _classify_fetch(argv: Sequence[str]) -> list[Finding]:
    args = list(argv[1:])
    for i, a in enumerate(args):
        if a in _UPLOAD_FLAGS or any(a.startswith(f + "=") for f in _UPLOAD_FLAGS):
            return _f("net.upload", f"upload flag {a}", argv)
        method = args[i + 1].lower() if i + 1 < len(args) else ""
        if a in ("-X", "--request", "-Method", "--method") and method in _MUTATING_METHODS:
            return _f("net.upload", f"HTTP {method.upper()}", argv)
    return _f("net.fetch", "network download", argv)


_DELETE_CMDS: Final = frozenset(
    {
        "rm",
        "rmdir",
        "del",
        "erase",
        "rd",
        "unlink",
        "shred",
        "remove-item",
        "ri",
        "rimraf",
        "clear-content",
    }
)
_WRITE_CMDS: Final = frozenset(
    {
        "cp",
        "copy",
        "mv",
        "move",
        "ren",
        "rename",
        "mkdir",
        "md",
        "touch",
        "tee",
        "ln",
        "mklink",
        "chmod",
        "chown",
        "icacls",
        "attrib",
        "new-item",
        "ni",
        "set-content",
        "add-content",
        "ac",
        "out-file",
        "copy-item",
        "cpi",
        "move-item",
        "mi",
        "rename-item",
        "rni",
        "expand-archive",
        "compress-archive",
        "tar",
        "unzip",
        "zip",
        "7z",
        "xcopy",
        "robocopy",
    }
)
_READ_CMDS: Final = frozenset(
    {
        "ls",
        "dir",
        "pwd",
        "echo",
        "cat",
        "type",
        "head",
        "tail",
        "more",
        "wc",
        "sort",
        "uniq",
        "cut",
        "tr",
        "diff",
        "cmp",
        "file",
        "stat",
        "du",
        "df",
        "tree",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "ag",
        "which",
        "where",
        "whoami",
        "hostname",
        "date",
        "uname",
        "ver",
        "printf",
        "basename",
        "dirname",
        "realpath",
        "readlink",
        "jq",
        "yq",
        "true",
        "false",
        "test",
        "cd",
        "get-childitem",
        "gci",
        "get-content",
        "gc",
        "get-location",
        "gl",
        "select-string",
        "sls",
        "get-item",
        "gi",
        "test-path",
        "get-process",
        "gps",
        "ps",
        "write-output",
        "write",
        "write-host",
        "get-command",
        "gcm",
        "measure-object",
        "measure",
        "select-object",
        "select",
        "where-object",
        "?",
        "sort-object",
        "format-table",
        "ft",
        "format-list",
        "fl",
        "out-string",
        "convertto-json",
        "convertfrom-json",
        "get-date",
        "resolve-path",
        "split-path",
        "join-path",
        "get-filehash",
        "get-itemproperty",
        "get-help",
        "tasklist",
        "systeminfo",
        "nproc",
        "id",
        "groups",
        "column",
        "less",
        "xxd",
        "od",
        "foreach-object",
        "%",
        "get-member",
        "group-object",
        "tee-object",
    }
)
_INLINE_CODE_FLAGS: Final = {
    "python": {"-c"},
    "python3": {"-c"},
    "py": {"-c"},
    "pypy": {"-c"},
    "node": {"-e", "--eval", "-p", "--print"},
    "deno": {"eval"},
    "bun": {"-e", "--eval"},
    "perl": {"-e", "-E"},
    "ruby": {"-e"},
    "php": {"-r"},
    "java": set(),
    "lua": {"-e"},
}
_WRAPPERS: Final = frozenset({"env", "time", "nice", "nohup", "timeout", "command"})
_PRIVILEGE: Final = frozenset(
    {
        "sudo",
        "doas",
        "su",
        "runas",
        "gsudo",
        "pkexec",
        "start-process",
        "start",
        "saps",
        "invoke-expression",
        "iex",
        "invoke-item",
        "ii",
        "xargs",
        "exec",
        "eval",
        "source",
        ".",
        "set-executionpolicy",
        "add-type",
        "start-job",
        "schtasks",
        "at",
        "crontab",
        "reg",
        "regedit",
        "set-itemproperty",
        "new-itemproperty",
        "sc",
        "net",
        "netsh",
        "powercfg",
        "shutdown",
        "restart-computer",
        "stop-computer",
        "taskkill",
        "stop-process",
        "kill",
        "pkill",
        "killall",
        "format",
        "diskpart",
        "bcdedit",
        "wmic",
        "mshta",
        "rundll32",
        "regsvr32",
        "certutil",
        "bitsadmin",
        "cscript",
        "wscript",
    }
)


# Note: "sc" is the Set-Content alias in PowerShell but service control (sc.exe) in cmd,
# so it is treated as a system change.


# ===================================================================== interpreters
def _ps_command_text(args: list[str]) -> str | None:
    i = 0
    while i < len(args):
        a = args[i]
        low = a.lower()
        if low in ("-command", "-c", "-cmd") or (len(low) > 2 and "-command".startswith(low)):
            return " ".join(args[i + 1 :])
        if low in ("-encodedcommand", "-e", "-ec", "-enc", "-en", "-encoded"):
            if i + 1 >= len(args):
                return None
            try:
                return base64.b64decode(args[i + 1]).decode("utf-16-le")
            except (binascii.Error, UnicodeDecodeError):
                return None
        if low in ("-file", "-f"):
            return None  # executes a script file: cannot analyse statically
        if low in (
            "-executionpolicy",
            "-ep",
            "-ex",
            "-windowstyle",
            "-w",
            "-inputformat",
            "-outputformat",
            "-configurationname",
            "-workingdirectory",
            "-wd",
        ):
            i += 2
            continue
        if low.startswith("-"):
            i += 1
            continue
        return " ".join(args[i:])  # bare script text
    return None


def _from_script(
    parsed: ParsedScript,
    argv: Sequence[str],
    depth: int,
    recurse: Callable[[Sequence[str], int], list[Finding]],
) -> list[Finding]:
    findings: list[Finding] = []
    if parsed.dynamic:
        findings += _unknown("dynamic script: " + "; ".join(parsed.reasons), argv)
    if parsed.writes_files:
        findings += _f("shell.write", "output redirection to a file", argv)
    if not parsed.commands and not parsed.dynamic:
        findings += _unknown("script contains no analysable commands", argv)
    for command in parsed.commands:
        findings += recurse(command, depth + 1)
    return findings


def classify_argv(
    argv: Sequence[str],
    *,
    allowed: Sequence[Sequence[str]] = (),
    depth: int = 0,
) -> list[Finding]:
    """Classify a single command. Always returns at least one finding."""
    if not argv:
        return _unknown("empty command", argv)
    if depth > MAX_DEPTH:
        return _unknown("nesting too deep", argv)

    def recurse(cmd: Sequence[str], d: int) -> list[Finding]:
        return classify_argv(cmd, allowed=allowed, depth=d)

    if argv[0] == DYNAMIC:
        return _unknown("dynamic command name", argv)
    exe = normalize_exe(argv[0])
    args = list(argv[1:])

    for prefix in allowed:
        if (
            prefix
            and len(argv) >= len(prefix)
            and normalize_exe(prefix[0]) == exe
            and list(argv[1 : len(prefix)]) == list(prefix[1:])
        ):
            return _f("build.test", "project-allowlisted command", argv)

    # ---- nested interpreters
    if exe in ("bash", "sh", "zsh", "dash", "ksh", "fish"):
        if "-c" in args:
            idx = args.index("-c")
            if idx + 1 < len(args):
                return _from_script(parse_bash(args[idx + 1]), argv, depth, recurse)
        return _unknown(f"{exe} runs a script file or interactive shell", argv)
    if exe in ("powershell", "pwsh"):
        text = _ps_command_text(args)
        if text is None:
            return _unknown(f"{exe} script file/encoded or unparseable command", argv)
        return _from_script(parse_powershell(text), argv, depth, recurse)
    if exe == "cmd":
        for i, a in enumerate(args):
            if a.lower() in ("/c", "/k", "/r"):
                return _from_script(parse_cmd(" ".join(args[i + 1 :])), argv, depth, recurse)
        return _unknown("cmd without /c", argv)
    if exe == "wsl":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] in ("-e", "--exec", "--"):
                i += 1
                break
            i += 2 if args[i] in ("-d", "--distribution", "-u", "--user", "--cd") else 1
        inner = args[i:]
        return recurse(inner, depth + 1) if inner else _unknown("interactive wsl", argv)
    if exe in _WRAPPERS:
        inner = list(args)
        while inner and (
            "=" in inner[0]
            or inner[0].startswith("-")
            or (exe == "timeout" and inner[0].replace(".", "").isdigit())
        ):
            inner.pop(0)
        if not inner:
            return _f("shell.read", f"{exe} (no command)", argv)
        return recurse(inner, depth + 1)
    if exe in ("uv", "poetry") and args[:1] == ["run"]:
        inner = [a for a in args[1:] if not a.startswith("-")][:1]
        if not inner:
            return _unknown(f"{exe} run without command", argv)
        idx = args.index(inner[0])
        return recurse(args[idx:], depth + 1)
    if exe in ("python", "python3", "py", "pypy"):
        if args[:1] == ["-m"] and len(args) > 1:
            module = args[1]
            if module in _PYTHON_SAFE_MODULES:
                return _f("build.test", f"python -m {module}", argv)
            if module == "pip":
                return recurse(["pip", *args[2:]], depth + 1)
            return _unknown(f"python -m {module}", argv)
        if len(args) == 1 and args[0] in ("--version", "-V"):
            return _f("shell.read", "version query", argv)
        return _unknown("python runs code", argv)
    inline = _INLINE_CODE_FLAGS.get(exe)
    if inline is not None and any(a in inline for a in args):
        return _unknown(f"{exe} inline code", argv)

    # ---- known families
    if exe in _PRIVILEGE:
        return _unknown(f"{exe} (privilege/dynamic execution/system change)", argv)
    if exe == "git":
        return _classify_git(argv)
    if exe in ("docker", "podman", "nerdctl", "docker-compose"):
        return _classify_docker(argv)
    if exe in ("kubectl", "oc", "helm"):
        return _classify_k8s(argv)
    if exe in ("terraform", "tofu", "pulumi"):
        sub = _positionals(args)[:1]
        if sub and sub[0] in (
            "plan",
            "validate",
            "fmt",
            "show",
            "output",
            "version",
            "providers",
            "graph",
            "preview",
            "whoami",
        ):
            return _f("shell.read", f"{exe} {sub[0]}", argv)
        return _f("infra.mutate", f"{exe} {' '.join(sub) or '(none)'}", argv)
    if exe in ("az", "aws", "gcloud"):
        pos = _positionals(args)
        if (
            pos
            and pos[-1] in ("list", "show", "describe", "get", "ls")
            and not any(
                p in ("create", "delete", "update", "set", "deploy", "put", "rm", "cp") for p in pos
            )
        ):
            return _f("shell.read", f"{exe} read", argv)
        return _f("infra.mutate", f"{exe} may change cloud resources", argv)
    if exe in _FETCH_TOOLS:
        return _classify_fetch(argv)
    if exe in _TRANSFER_TOOLS:
        return _f("net.upload", f"{exe} transfers files", argv)
    if exe in _REMOTE_EXEC:
        return _unknown(f"{exe} remote execution/raw network", argv)

    pkg = _classify_package_or_build(exe, argv)
    if pkg is not None:
        return pkg
    if exe in _TEST_TOOLS:
        return _f("build.test", f"{exe}", argv)
    if exe in _DELETE_CMDS:
        return _f("shell.delete", f"{exe} deletes files", argv)
    if exe == "find" and any(
        a in ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fls") for a in args
    ):
        return _unknown("find with -exec/-delete", argv)
    if exe == "sed" and any(a == "-i" or a.startswith("-i") or a == "--in-place" for a in args):
        return _f("shell.write", "sed -i edits files", argv)
    if exe in _WRITE_CMDS:
        return _f("shell.write", f"{exe} writes files", argv)
    if exe in _READ_CMDS or exe == "find" or exe == "sed":
        return _f("shell.read", f"{exe}", argv)
    if len(args) == 1 and args[0] in ("--version", "-V", "-v", "version", "-version"):
        return _f("shell.read", "version query", argv)
    return _unknown(f"unrecognised command {exe!r}", argv)
