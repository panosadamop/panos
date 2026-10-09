"""Vector storage and hybrid (RRF) search in the knowledge index (ADR-017, FR-020)."""

from pathlib import Path

import pytest

from panos_core.knowledge.index import KnowledgeIndex, document_from_bytes

pytestmark = pytest.mark.req("FR-020", "FR-019")


def build(tmp_path: Path, *, vec: bool) -> tuple[KnowledgeIndex, dict[str, int]]:
    index = KnowledgeIndex(tmp_path / "k.db")
    if not vec:
        index.vector_extension = False  # force the pure-Python cosine fallback
    docs = [
        document_from_bytes(rel, body.encode())
        for rel, body in (
            ("billing.py", "def charge_card(amount): pass  # payments"),
            ("orders.py", "class OrderService: handles παραγγελίες"),
            ("readme.md", "Project overview and setup"),
        )
    ]
    index.update(
        "prj_1",
        "LOCAL_ONLY",
        [d for d in docs if d is not None],
        {"billing.py", "orders.py", "readme.md"},
    )
    pending = index.pending_embeddings("prj_1", "m", limit=10)
    ids = {text.split()[1].split("(")[0].rstrip(":"): cid for cid, text in pending}
    vectors = {
        "charge_card": [1.0, 0.0, 0.0],
        "OrderService": [0.0, 1.0, 0.0],
        "overview": [0.0, 0.0, 1.0],
    }
    index.store_embeddings("m", [(cid, vectors[name]) for name, cid in ids.items()])
    return index, ids


@pytest.mark.parametrize("vec", [True, False])
def test_vectors_are_incremental_and_hybrid_search_fuses_rankings(
    tmp_path: Path, vec: bool
) -> None:
    index, ids = build(tmp_path, vec=vec)
    assert set(ids) == {"charge_card", "OrderService", "overview"}
    assert index.pending_embeddings("prj_1", "m") == []
    assert index.pending_embeddings("prj_1", "other-model")  # per model
    assert index.embedded_count("prj_1", "m") == 3
    # Semantic query: no lexical overlap with "payments"-free wording, vector finds billing.
    hits = index.hybrid_search("money", [0.9, 0.1, 0.0], model="m", project_id="prj_1")
    assert hits[0].uri == "billing.py"
    # Lexical + vector agree: fused score ranks orders first.
    fused = index.hybrid_search("OrderService", [0.0, 1.0, 0.0], model="m", project_id="prj_1")
    assert fused[0].uri == "orders.py" and fused[0].score > 0
    # Without a query vector it degrades to lexical.
    assert index.hybrid_search("overview", None, model=None)[0].uri == "readme.md"
    assert index.hybrid_search("money", [0.9, 0.1, 0.0], model="m", project_id="prj_2") == []


def test_removed_files_drop_their_embeddings(tmp_path: Path) -> None:
    index, _ = build(tmp_path, vec=True)
    index.update("prj_1", "LOCAL_ONLY", [], {"orders.py"})
    assert index.embedded_count("prj_1", "m") == 1
