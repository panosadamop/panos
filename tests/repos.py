"""Helpers for working with the fixture repositories in ``tests/fixtures/repos``."""

import shutil
import subprocess
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "repos"


def copy_repo(name: str, dest: Path, *, git: bool = False) -> Path:
    """Copy a fixture repo to ``dest/name`` (optionally as a committed Git repo)."""
    target = dest / name
    shutil.copytree(FIXTURES / name, target)
    if git and shutil.which("git"):

        def run(*args: str) -> None:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(target),
                    "-c",
                    "user.email=t@example.invalid",
                    "-c",
                    "user.name=Tester",
                    *args,
                ],
                check=True,
                capture_output=True,
            )

        run("init", "-q", "-b", "main")
        run("add", ".")
        run("commit", "-q", "-m", "initial import")
    return target
