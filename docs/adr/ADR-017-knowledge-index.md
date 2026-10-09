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
