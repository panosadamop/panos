"""Local knowledge index (FR-019 partial, FR-020, ADR-017). Lexical (FTS5) in M3; vectors in M4."""

from panos_core.knowledge.index import IndexStats, KnowledgeIndex, SearchHit
from panos_core.knowledge.service import KnowledgeService

__all__ = ["IndexStats", "KnowledgeIndex", "KnowledgeService", "SearchHit"]
