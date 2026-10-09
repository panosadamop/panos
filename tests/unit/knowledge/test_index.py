"""Lexical knowledge index (FR-020, NFR-PRV-003, NFR-PERF-004)."""

from pathlib import Path

import pytest

from panos_core.knowledge.index import (
    SCHEMA_VERSION,
    Document,
    KnowledgeIndex,
    chunk_text,
    document_from_bytes,
    fts_query,
)

pytestmark = pytest.mark.req("FR-020", "NFR-PRV-003")


def doc(uri: str, text: str) -> Document:
    d = document_from_bytes(uri, text.encode("utf-8"))
    assert d is not None
    return d


@pytest.fixture
def index(tmp_path: Path) -> KnowledgeIndex:
    return KnowledgeIndex(tmp_path / "k.db")


def test_chunking_tracks_line_ranges() -> None:
    text = "\n".join(f"line {i}" for i in range(1, 121))
    chunks = chunk_text(text, size=50)
    assert [(s, e) for s, e, _ in chunks] == [(1, 50), (51, 100), (101, 120)]


def test_binary_and_oversized_files_are_skipped() -> None:
    assert document_from_bytes("a.bin", b"\x00\x01\x02") is None
    assert document_from_bytes("big.txt", b"x" * 2_000_000) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("payment retry", '"payment" AND "retry"*'),
        ("Παραγγελίες", '"παραγγελιεσ"*'),
        ('weird "quotes" AND OR NEAR(', '"weird" AND "quotes" AND "and" AND "or" AND "near"*'),
        ("!!!", None),
    ],
)
def test_fts_query_is_sanitised(raw: str, expected: str | None) -> None:
    assert fts_query(raw) == expected


def test_search_returns_provenance(index: KnowledgeIndex) -> None:
    index.update(
        "prj_1",
        "LOCAL_ONLY",
        [doc("src/pay.py", "def retry_payment():\n    pass\n")],
        {"src/pay.py"},
    )
    hits = index.search("retry payment", project_id="prj_1")
    assert len(hits) == 1
    hit = hits[0]
    assert (hit.uri, hit.start_line, hit.end_line) == ("src/pay.py", 1, 2)
    assert hit.privacy_mode == "LOCAL_ONLY"
    assert len(hit.content_hash) == 64 and hit.indexed_at
    assert hit.snippet == "def retry_payment():"


def test_greek_search_ignores_accents_and_case(index: KnowledgeIndex) -> None:
    index.update(
        "prj_1",
        "LOCAL_ONLY",
        [doc("README.md", "Σύστημα Παραγγελιών για τον πελάτη")],
        {"README.md"},
    )
    assert index.search("συστημα παραγγελιων", project_id="prj_1")
    hits = index.search("ΠΕΛΑΤΗ", project_id="prj_1")
    assert hits[0].snippet == "Σύστημα Παραγγελιών για τον πελάτη"  # original text shown


@pytest.mark.req("NFR-PERF-004")
def test_incremental_update_and_removal(index: KnowledgeIndex) -> None:
    a, b = doc("a.md", "alpha"), doc("b.md", "beta")
    assert index.update("p", "LOCAL_ONLY", [a, b], {"a.md", "b.md"})[:2] == (2, 0)
    assert index.known_hashes("p") == {"a.md": a.content_hash, "b.md": b.content_hash}
    a2 = doc("a.md", "alpha changed")
    indexed, removed, _ = index.update("p", "LOCAL_ONLY", [a2], {"a.md"})
    assert (indexed, removed) == (1, 1)  # a.md re-chunked, b.md gone
    assert not index.search("beta", project_id="p")
    assert index.search("changed", project_id="p")
    assert index.stats("p") == {"sources": 1, "chunks": 1}


def test_projects_are_isolated(index: KnowledgeIndex) -> None:
    index.update("p1", "LOCAL_ONLY", [doc("x.md", "secret plan")], {"x.md"})
    index.update("p2", "STANDARD", [doc("y.md", "public plan")], {"y.md"})
    assert {h.uri for h in index.search("plan", project_id="p2")} == {"y.md"}
    assert {h.uri for h in index.search("plan")} == {"x.md", "y.md"}
    index.remove_project("p1")
    assert {h.uri for h in index.search("plan")} == {"y.md"}


def test_privacy_label_follows_project_mode(index: KnowledgeIndex) -> None:
    index.update("p", "STANDARD", [doc("x.md", "hello")], {"x.md"})
    index.update("p", "LOCAL_ONLY", [], {"x.md"})
    assert index.search("hello", project_id="p")[0].privacy_mode == "LOCAL_ONLY"


def test_schema_version_mismatch_rebuilds(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "k.db"
    KnowledgeIndex(path)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 99")
    KnowledgeIndex(path)  # must not raise; derived cache is rebuilt
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
