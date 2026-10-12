"""SQLite FTS5 knowledge index.

* Separate ``knowledge.db`` (ADR-017): a derived, rebuildable cache, so it uses a simple
  ``PRAGMA user_version`` schema version instead of Alembic.
* Incremental: a file is re-chunked only when its SHA-256 changes (NFR-PERF-004).
* Provenance on every chunk: source URI, line range, content hash, privacy mode and
  index time (NFR-PRV-003). LOCAL_ONLY sources keep their label for M4 taint tracking.
* Text is normalised before indexing and querying (NFD, combining marks removed,
  casefolded): FTS5's ``remove_diacritics`` only folds Latin-script accents, so this is
  what makes Greek match without tonos/case (``Παραγγελιών`` ≈ ``παραγγελιων``).
* Optional vectors (ADR-017, M4): embeddings are stored per chunk and searched with the
  ``sqlite-vec`` SQL functions (pure-Python cosine fallback if the extension cannot be
  loaded). Hybrid search fuses lexical and vector rankings with reciprocal rank fusion.
"""

import hashlib
import math
import re
import sqlite3
import threading
import unicodedata
from array import array
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

SCHEMA_VERSION: Final = 4
RRF_K: Final = 60
CHUNK_LINES: Final = 50
MAX_FILE_BYTES: Final = 1_000_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    uri TEXT NOT NULL,
    provider TEXT NOT NULL,
    privacy_mode TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size INTEGER NOT NULL,
    indexed_at TEXT NOT NULL,
    UNIQUE (project_id, uri)
);
CREATE TABLE IF NOT EXISTS chunk (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chunk_source ON chunk(source_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    text, chunk_id UNINDEXED, raw UNINDEXED, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS chunk_embedding (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunk(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL
);
"""

_TOKEN_RE: Final = re.compile(r"\w+", re.UNICODE)


def normalize(text: str) -> str:
    """Accent- and case-insensitive form used for matching (all scripts, incl. Greek)."""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return stripped.casefold()


@dataclass(frozen=True)
class IndexStats:
    files_seen: int
    indexed: int
    unchanged: int
    removed: int
    chunks: int


@dataclass(frozen=True)
class SearchHit:
    project_id: str
    uri: str
    start_line: int
    end_line: int
    snippet: str
    score: float
    privacy_mode: str
    content_hash: str
    indexed_at: str


@dataclass(frozen=True)
class Document:
    uri: str
    text: str
    content_hash: str
    size: int


def chunk_text(text: str, size: int = CHUNK_LINES) -> list[tuple[int, int, str]]:
    lines = text.splitlines()
    return [
        (i + 1, min(i + size, len(lines)), "\n".join(lines[i : i + size]))
        for i in range(0, len(lines), size)
        if "".join(lines[i : i + size]).strip()
    ]


def make_snippet(raw: str, terms: list[str], *, width: int = 200) -> str:
    """The first original-text line containing a query term (accent/case-insensitive)."""
    for line in raw.splitlines():
        folded = normalize(line)
        if any(term in folded for term in terms):
            text = line.strip()
            return text if len(text) <= width else text[: width - 1] + "…"
    first = raw.strip().splitlines()[0] if raw.strip() else ""
    return first[:width]


def fts_query(raw: str) -> str | None:
    """Turn free text into a safe FTS5 query (all terms must match, prefix on the last)."""
    tokens = _TOKEN_RE.findall(normalize(raw))[:16]
    if not tokens:
        return None
    quoted = [f'"{t}"' for t in tokens]
    quoted[-1] += "*"
    return " AND ".join(quoted)


class KnowledgeIndex:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                conn.executescript(
                    "DROP TABLE IF EXISTS chunk_embedding; DROP TABLE IF EXISTS chunk_fts;"
                    " DROP TABLE IF EXISTS chunk;"
                    " DROP TABLE IF EXISTS source;"
                )
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        if self.vector_extension is not False:
            self.vector_extension = _load_vec(conn)
        return conn

    vector_extension: bool | None = None  # None = not tried yet

    def known_hashes(self, project_id: str) -> dict[str, str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT uri, content_hash FROM source WHERE project_id = ?", (project_id,)
            ).fetchall()
        return {uri: h for uri, h in rows}

    def update(
        self,
        project_id: str,
        privacy_mode: str,
        changed: Iterable[Document],
        seen_uris: set[str],
        *,
        provider: str = "filesystem",
    ) -> tuple[int, int, int]:
        """Upsert changed documents and drop sources no longer present.

        Returns (documents indexed, sources removed, chunks written).
        """
        now = datetime.now(UTC).isoformat()
        indexed = chunks = 0
        with self._lock, self._connect() as conn:
            for doc in changed:
                self._delete_source(conn, project_id, doc.uri)
                cur = conn.execute(
                    "INSERT INTO source (project_id, uri, provider, privacy_mode, content_hash,"
                    " size, indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (project_id, doc.uri, provider, privacy_mode, doc.content_hash, doc.size, now),
                )
                source_id = cur.lastrowid
                for start, end, text in chunk_text(doc.text):
                    ccur = conn.execute(
                        "INSERT INTO chunk (source_id, start_line, end_line) VALUES (?, ?, ?)",
                        (source_id, start, end),
                    )
                    conn.execute(
                        "INSERT INTO chunk_fts (text, chunk_id, raw) VALUES (?, ?, ?)",
                        (normalize(text), ccur.lastrowid, text),
                    )
                    chunks += 1
                indexed += 1
            stale = [
                uri
                for (uri,) in conn.execute(
                    "SELECT uri FROM source WHERE project_id = ?", (project_id,)
                )
                if uri not in seen_uris
            ]
            for uri in stale:
                self._delete_source(conn, project_id, uri)
            conn.execute(
                "UPDATE source SET privacy_mode = ? WHERE project_id = ?",
                (privacy_mode, project_id),
            )
        return indexed, len(stale), chunks

    @staticmethod
    def _delete_source(conn: sqlite3.Connection, project_id: str, uri: str) -> None:
        row = conn.execute(
            "SELECT id FROM source WHERE project_id = ? AND uri = ?", (project_id, uri)
        ).fetchone()
        if row is None:
            return
        conn.execute(
            "DELETE FROM chunk_fts WHERE chunk_id IN (SELECT id FROM chunk WHERE source_id = ?)",
            (row[0],),
        )
        conn.execute("DELETE FROM source WHERE id = ?", (row[0],))

    def relabel(self, project_id: str, privacy_mode: str) -> None:
        """Apply a changed privacy label to every indexed source of a scope."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE source SET privacy_mode = ? WHERE project_id = ?",
                (privacy_mode, project_id),
            )

    def remove_project(self, project_id: str) -> None:
        with self._lock, self._connect() as conn:
            for (uri,) in conn.execute(
                "SELECT uri FROM source WHERE project_id = ?", (project_id,)
            ).fetchall():
                self._delete_source(conn, project_id, uri)

    # ------------------------------------------------------------------ lexical
    @staticmethod
    def _lexical(
        conn: sqlite3.Connection, query: str, project_id: str | None, limit: int
    ) -> list[tuple[int, float]]:
        match = fts_query(query)
        if match is None:
            return []
        sql = (
            "SELECT chunk_fts.chunk_id, bm25(chunk_fts) FROM chunk_fts"
            " JOIN chunk c ON c.id = chunk_fts.chunk_id"
            " JOIN source s ON s.id = c.source_id WHERE chunk_fts MATCH ?"
        )
        params: list[object] = [match]
        if project_id is not None:
            sql += " AND s.project_id = ?"
            params.append(project_id)
        sql += " ORDER BY bm25(chunk_fts) LIMIT ?"
        params.append(limit)
        return [(int(cid), -float(rank)) for cid, rank in conn.execute(sql, params)]

    @staticmethod
    def _hits(
        conn: sqlite3.Connection, ranked: list[tuple[int, float]], query: str
    ) -> list[SearchHit]:
        terms = _TOKEN_RE.findall(normalize(query))
        hits = []
        for chunk_id, score in ranked:
            row = conn.execute(
                "SELECT s.project_id, s.uri, c.start_line, c.end_line, f.raw, s.privacy_mode,"
                " s.content_hash, s.indexed_at FROM chunk c JOIN source s ON s.id = c.source_id"
                " JOIN chunk_fts f ON f.chunk_id = c.id WHERE c.id = ?",
                (chunk_id,),
            ).fetchone()
            if row is not None:
                hits.append(
                    SearchHit(
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                        make_snippet(row[4], terms),
                        score,
                        row[5],
                        row[6],
                        row[7],
                    )
                )
        return hits

    def search(
        self, query: str, *, project_id: str | None = None, limit: int = 20
    ) -> list[SearchHit]:
        limit = max(1, min(limit, 200))
        with self._lock, self._connect() as conn:
            return self._hits(conn, self._lexical(conn, query, project_id, limit), query)

    # ------------------------------------------------------------------ vectors
    def pending_embeddings(
        self, project_id: str, model: str, limit: int = 64
    ) -> list[tuple[int, str]]:
        """Chunks of a project that have no embedding for ``model`` yet: (chunk_id, text)."""
        with self._lock, self._connect() as conn:
            return [
                (int(cid), str(raw))
                for cid, raw in conn.execute(
                    "SELECT c.id, f.raw FROM chunk c JOIN source s ON s.id = c.source_id"
                    " JOIN chunk_fts f ON f.chunk_id = c.id"
                    " LEFT JOIN chunk_embedding e ON e.chunk_id = c.id AND e.model = ?"
                    " WHERE s.project_id = ? AND e.chunk_id IS NULL LIMIT ?",
                    (model, project_id, limit),
                )
            ]

    def store_embeddings(self, model: str, rows: list[tuple[int, list[float]]]) -> None:
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO chunk_embedding (chunk_id, model, dim, vec)"
                " VALUES (?, ?, ?, ?)",
                [(cid, model, len(vec), _pack(vec)) for cid, vec in rows],
            )

    def embedded_count(self, project_id: str, model: str) -> int:
        with self._lock, self._connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM chunk_embedding e JOIN chunk c ON c.id = e.chunk_id"
                    " JOIN source s ON s.id = c.source_id WHERE s.project_id = ? AND e.model = ?",
                    (project_id, model),
                ).fetchone()[0]
            )

    def _vector(
        self,
        conn: sqlite3.Connection,
        query_vec: list[float],
        project_id: str | None,
        model: str,
        limit: int,
    ) -> list[tuple[int, float]]:
        where = "e.model = ?" + (" AND s.project_id = ?" if project_id else "")
        params: list[object] = [model, *([project_id] if project_id else [])]
        base = (
            "FROM chunk_embedding e JOIN chunk c ON c.id = e.chunk_id"
            f" JOIN source s ON s.id = c.source_id WHERE {where}"
        )
        if self.vector_extension:
            rows = conn.execute(
                f"SELECT e.chunk_id, vec_distance_cosine(e.vec, ?) AS d {base} ORDER BY d LIMIT ?",
                [_pack(query_vec), *params, limit],
            ).fetchall()
            return [(int(cid), 1.0 - float(d)) for cid, d in rows]
        scored = [
            (int(cid), _cosine(query_vec, _unpack(blob)))
            for cid, blob in conn.execute(f"SELECT e.chunk_id, e.vec {base}", params)
        ]
        return sorted(scored, key=lambda x: -x[1])[:limit]

    def hybrid_search(
        self,
        query: str,
        query_vec: list[float] | None,
        *,
        model: str | None,
        project_id: str | None = None,
        limit: int = 20,
    ) -> list[SearchHit]:
        """Reciprocal rank fusion of lexical and vector rankings (lexical-only without vec)."""
        limit = max(1, min(limit, 200))
        with self._lock, self._connect() as conn:
            lexical = self._lexical(conn, query, project_id, limit * 3)
            if query_vec is None or model is None:
                return self._hits(conn, lexical[:limit], query)
            vector = self._vector(conn, query_vec, project_id, model, limit * 3)
            fused: dict[int, float] = {}
            for ranking in (lexical, vector):
                for rank, (cid, _score) in enumerate(ranking):
                    fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            ranked = sorted(fused.items(), key=lambda x: -x[1])[:limit]
            return self._hits(conn, [(cid, round(score, 6)) for cid, score in ranked], query)

    def stats(self, project_id: str) -> dict[str, int]:
        with self._lock, self._connect() as conn:
            sources = conn.execute(
                "SELECT COUNT(*) FROM source WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
            chunks = conn.execute(
                "SELECT COUNT(*) FROM chunk c JOIN source s ON s.id = c.source_id"
                " WHERE s.project_id = ?",
                (project_id,),
            ).fetchone()[0]
        return {"sources": int(sources), "chunks": int(chunks)}


def document_from_bytes(uri: str, data: bytes) -> Document | None:
    """Text files, plus Office/PDF files through their extractors (M8-04). Other binary
    content (NUL bytes) and oversized files are skipped."""
    from panos_core.knowledge.extract import extract_text, extractable

    text: str | None
    if extractable(uri):
        text = extract_text(uri, data)
    elif len(data) > MAX_FILE_BYTES or b"\x00" in data[:8192]:
        return None
    else:
        text = data.decode("utf-8", errors="replace")
    if text is None:
        return None
    return Document(
        uri=uri,
        text=text,
        content_hash=hashlib.sha256(data).hexdigest(),
        size=len(data),
    )


def _load_vec(conn: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (ImportError, AttributeError, sqlite3.OperationalError):
        return False
    return True


def _pack(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()  # float32, the layout sqlite-vec expects


def _unpack(blob: bytes) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    return list(values)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
