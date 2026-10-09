# ADR-017: Knowledge index

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** FR-019, FR-020, KNOWLEDGE_MEMORY, NFR-PRV-003, NFR-PERF-004, MVP#17

## Context
Hybrid lexical and vector retrieval with provenance is needed, offline, with Greek and English content, and must not require a separate server.

## Decision
- **Lexical:** SQLite FTS5.
- **Vectors:** `sqlite-vec`, behind a `VectorIndex` port (fallback: brute-force cosine for small corpora).
- **Fusion:** reciprocal rank fusion (RRF).
- **Embeddings:** local multilingual model via `fastembed` (ONNX, no torch), or Ollama embeddings. Chosen by a small Greek/English retrieval benchmark in M4.
- **Indexing:** incremental by content hash; honors `.gitignore`.
- **Provenance per chunk:** URI/path, hash, version, timestamp and privacy label.
- Separate `knowledge.db`. LOCAL_ONLY embeddings are never exported or synced.
- Retrieval order: current project → company → past approved projects → user knowledge → approved external → general model knowledge.

## Alternatives considered
Chroma, Qdrant or LanceDB (extra services or dependencies; can be added later behind the port).

## Consequences
`sqlite-vec` is pre-1.0; the port isolates the risk.

## Implementation notes (M3, 2026-10-09)

- **Lexical index delivered:** SQLite FTS5 in `knowledge.db`, a derived cache versioned with `PRAGMA user_version`. A mismatched version is rebuilt, never migrated.
- **Accent- and case-insensitive matching, including Greek.** FTS5's `remove_diacritics` folds only Latin-script accents, so Panos indexes and queries a normalized form (NFD, combining marks removed, casefolded; ς→σ). The original chunk text is stored alongside it (`raw`, unindexed), so snippets show the real text.
- Free-text queries are converted into quoted terms joined with AND, with a prefix match on the last term. FTS5 operators typed by users or models are therefore treated as plain terms.
- Provenance per hit: URI, line range, content hash, privacy mode and index time. A project's privacy mode is re-applied to all its sources on every index run.
- Vectors (`sqlite-vec` + local embeddings) and RRF fusion remain M4 scope.

## Implementation notes (M4, 2026-10-09)

- Schema v4 adds `chunk_embedding(chunk_id, model, dim, vec float32 BLOB)`, cascading with its chunk. Embeddings are keyed per model and computed incrementally (`project index --semantic`).
- Vector search uses `sqlite-vec`'s `vec_distance_cosine`, with a pure-Python cosine fallback if the extension cannot be loaded. Hybrid search fuses the lexical (bm25) and vector rankings with reciprocal rank fusion (k = 60).
- Without embeddings, or if the embedding model is unavailable, search silently degrades to lexical.
- `nomic-embed-text` (768 dims) via Ollama, with its `search_query:` / `search_document:` prefixes, replaces fastembed. It avoids a second ONNX runtime, and the model was already pulled in S-1.
