"""Child-process environment and executable resolution (ADR-007)."""

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from panos_core.redaction import is_sensitive_key

_SENSITIVE_PREFIXES: Final = (
    "AWS_",
    "AZURE_",
    "ARM_CLIENT",
    "GOOGLE_",
    "GCP_",
    "PANOS_",
    "OPENAI_",
    "ANTHROPIC_",
    "HF_",
    "HUGGING",
    "GH_",
    "GITHUB_",
    "GITLAB_",
    "NPM_",
    "NUGET_",
    "DOCKER_AUTH",
    "VAULT_",
)
_SENSITIVE_SUFFIXES: Final = ("_KEY", "_PAT", "_PWD", "_PASS", "_CREDENTIALS")
_VALID_NAME: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Characters cmd.exe interprets even inside quoted batch-file arguments (CVE-2024-24576
# "BatBadBut"). Arguments containing them are refused for .bat/.cmd targets.
_BATCH_UNSAFE: Final = frozenset('&|<>^%!"\r\n')


def is_sensitive_env(name: str) -> bool:
    upper = name.upper()
    return (
        is_sensitive_key(name)
        or upper.startswith(_SENSITIVE_PREFIXES)
        or upper.endswith(_SENSITIVE_SUFFIXES)
    )


def build_child_env(
    base: Mapping[str, str], overrides: Mapping[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Return (environment, names removed). Secret-looking variables are dropped.

    Overrides must have valid, non-sensitive names. On Windows,
    ``NoDefaultCurrentDirectoryInExePath`` stops child shells from resolving executables
    from the current directory.
    """
    removed = sorted(k for k in base if is_sensitive_env(k))
    env = {k: v for k, v in base.items() if not is_sensitive_env(k)}
    for name, value in overrides.items():
        if not _VALID_NAME.match(name):
            raise ValueError(f"invalid environment variable name {name!r}")
        if is_sensitive_env(name):
            raise ValueError(f"refusing to pass sensitive variable {name!r}")
        if "\x00" in value:
            raise ValueError("NUL byte in environment value")
        env[name] = value
    if os.name == "nt":
        env["NoDefaultCurrentDirectoryInExePath"] = "1"
    return env, removed


def resolve_executable(name: str, path_env: str, *, cwd: Path, root: Path) -> Path | None:
    """Find an executable without ever consulting the current directory implicitly.

    * bare names are searched on ``path_env`` only (with PATHEXT on Windows);
    * names with a path separator are resolved relative to ``cwd`` and must stay inside
      the project ``root`` (e.g. ``./gradlew``, ``vendor/bin/phpunit``), or be absolute.
    """
    if not name or "\x00" in name:
        return None
    windows = os.name == "nt"
    exts = [""]
    if windows:
        exts += [
            e.lower() for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e
        ]

    def candidates(base: Path) -> list[Path]:
        if windows and base.suffix:
            return [base]
        return [base.with_name(base.name + ext) for ext in exts]

    if "/" in name or "\\" in name:
        raw = Path(name)
        base = raw if raw.is_absolute() else (cwd / raw)
        for cand in candidates(base):
            resolved = cand.resolve()
            if not raw.is_absolute() and root.resolve() not in resolved.parents:
                return None
            if resolved.is_file() and (windows or os.access(resolved, os.X_OK)):
                return resolved
        return None

    for directory in path_env.split(os.pathsep):
        if not directory:
            continue
        for cand in candidates(Path(directory) / name):
            if cand.is_file() and (windows or os.access(cand, os.X_OK)):
                return cand
    return None


def batch_argument_problem(executable: Path, args: list[str]) -> str | None:
    """Return a reason if ``args`` are unsafe to pass to a Windows batch file."""
    if executable.suffix.lower() not in (".bat", ".cmd"):
        return None
    for arg in args:
        bad = sorted(set(arg) & _BATCH_UNSAFE)
        if bad:
            return f"argument contains characters unsafe for batch files: {''.join(bad)!r}"
    return None
