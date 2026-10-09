"""Build the packaged node (``panos-node``) with PyInstaller (ADR-021, M7-09).

One-folder build (fast start-up, no temp extraction). The result lands in
``apps/desktop/src-tauri/sidecar/panos-node/`` and is bundled by Tauri as a resource; the
desktop shell finds it next to its own executable.

Usage: ``uv run python scripts/build_sidecar.py [--no-voice]``
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "apps" / "desktop" / "src-tauri" / "sidecar"
WORK = ROOT / "build" / "pyinstaller"
MIGRATIONS = ROOT / "packages/panos-core/src/panos_core/persistence/migrations"


def main() -> None:
    voice = "--no-voice" not in sys.argv
    entry = WORK / "panos_node_entry.py"
    WORK.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "from panos_cli.main import main\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(entry),
        "--name",
        "panos-node",
        "--onedir",
        "--noconfirm",
        "--clean",
        "--console",
        "--distpath",
        str(OUT),
        "--workpath",
        str(WORK / "work"),
        "--specpath",
        str(WORK),
        # Package data: policies, model registry, stack packs, templates, migrations.
        "--collect-data",
        "panos_core",
        "--collect-submodules",
        "panos_core",
        "--collect-submodules",
        "panos_node_api",
        "--collect-submodules",
        "panos_cli",
        "--collect-submodules",
        "uvicorn",
        "--collect-data",
        "alembic",
        # Alembic loads env.py and the migration scripts as source files from disk.
        "--add-data",
        f"{MIGRATIONS}{os.pathsep}panos_core/persistence/migrations",
        "--hidden-import",
        "keyring.backends.Windows",
        # SQLAlchemy imports its DB driver by dialect name at engine creation.
        "--hidden-import",
        "aiosqlite",
        "--hidden-import",
        "win32timezone",
    ]
    if voice:
        args += [
            "--collect-all",
            "piper",
            "--collect-data",
            "faster_whisper",
            "--collect-binaries",
            "ctranslate2",
            "--collect-binaries",
            "onnxruntime",
            "--collect-binaries",
            "sounddevice",
            "--collect-data",
            "_sounddevice_data",
            "--collect-binaries",
            "soundfile",
            "--collect-data",
            "_soundfile_data",
        ]
    else:
        for module in ("faster_whisper", "ctranslate2", "piper", "onnxruntime", "sounddevice"):
            args += ["--exclude-module", module]
    if OUT.exists():
        shutil.rmtree(OUT / "panos-node", ignore_errors=True)
    subprocess.run(args, check=True, cwd=ROOT)  # noqa: S603 - fixed argv, no user input
    exe = OUT / "panos-node" / ("panos-node.exe" if sys.platform == "win32" else "panos-node")
    size = sum(f.stat().st_size for f in (OUT / "panos-node").rglob("*") if f.is_file())
    print(f"built {exe} ({size / 2**20:.0f} MB, voice={'yes' if voice else 'no'})")


if __name__ == "__main__":
    main()
