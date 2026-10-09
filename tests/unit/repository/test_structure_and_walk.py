from pathlib import Path

import pytest

from panos_core.repository.gitcli import parse_log, parse_status_v2
from panos_core.repository.structure import (
    conventions,
    documentation,
    outline,
    python_packages,
    tree_summary,
)
from panos_core.repository.walk import iter_project_files
from tests.repos import FIXTURES

pytestmark = pytest.mark.req("FR-005")


def test_walk_honours_gitignore_and_skips_vendor_dirs(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("secrets/\n*.log\n", encoding="utf-8")
    for rel in (
        "src/a.py",
        "secrets/key.txt",
        "debug.log",
        "node_modules/x/i.js",
        ".venv/lib/x.py",
        "docs/readme.md",
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x", encoding="utf-8")
    rels = {f.rel for f in iter_project_files(tmp_path)}
    assert rels == {".gitignore", "src/a.py", "docs/readme.md"}


def test_walk_respects_max_files(tmp_path: Path) -> None:
    for i in range(20):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    assert len(list(iter_project_files(tmp_path, max_files=5))) == 5


def test_outline_python_typescript_java_php() -> None:
    files = []
    for name in ("python_fastapi", "react_vite_pnpm", "spring_maven", "laravel"):
        files += list(iter_project_files(FIXTURES / name))
    out = outline(files)
    assert out["src/orders/api.py"] == ["OrderService", "list_orders"]
    assert out["src/api.ts"] == ["fetchOrders", "Client"]
    assert out["src/App.tsx"] == ["App", "VERSION"]
    assert out["src/main/java/com/acme/ContractRepository.java"] == ["ContractRepository"]
    assert out["app/Models/Invoice.php"] == ["Invoice"]


def test_outline_never_contains_source_bodies() -> None:
    files = list(iter_project_files(FIXTURES / "python_fastapi"))
    flattened = " ".join(s for symbols in outline(files).values() for s in symbols)
    assert "return" not in flattened and "FastAPI()" not in flattened


def test_tree_docs_conventions_packages() -> None:
    files = list(iter_project_files(FIXTURES / "python_fastapi"))
    tree = tree_summary(files)
    assert tree["total_files"] == len(files)
    assert "src/" in tree["top_level"]
    assert conventions(files) == ["EditorConfig"]
    docs = documentation(files)
    assert docs["readme"] == "README.md"
    assert python_packages(files) == ["src/orders"]


def test_parse_status_v2() -> None:
    text = (
        "\x00".join(
            [
                "# branch.oid 1234567890abcdef1234567890abcdef12345678",
                "# branch.head feature/x",
                "# branch.upstream origin/feature/x",
                "# branch.ab +2 -1",
                "1 .M N... 100644 100644 100644 aaa bbb src/app.py",
                "2 R. N... 100644 100644 100644 ccc ddd R100 new name.py",
                "old name.py",
                "? notes.txt",
            ]
        )
        + "\x00"
    )
    status = parse_status_v2(text)
    assert status.branch == "feature/x"
    assert (status.ahead, status.behind) == (2, 1)
    assert [e.path for e in status.entries] == ["src/app.py", "new name.py", "notes.txt"]
    assert status.entries[1].orig_path == "old name.py"
    assert status.dirty


def test_parse_status_initial_and_detached() -> None:
    status = parse_status_v2("# branch.oid (initial)\x00# branch.head (detached)\x00")
    assert status.head is None and status.branch is None and not status.dirty


def test_parse_log() -> None:
    record = "\x1f".join(["a" * 40, "aaaaaaa", "Jane", "2026-10-08T10:00:00+03:00", "feat: x"])
    commits = parse_log(record + "\x1e\n")
    assert len(commits) == 1
    assert commits[0].subject == "feat: x"
