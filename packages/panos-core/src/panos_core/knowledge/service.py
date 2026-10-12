"""Knowledge service: incremental project indexing and (hybrid) search with provenance."""

import asyncio
import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from panos_core.audit import AuditService
from panos_core.domain import Project
from panos_core.domain.enums import PrivacyMode
from panos_core.knowledge.extract import MAX_DOCUMENT_BYTES, extractable
from panos_core.knowledge.git_history import history_document
from panos_core.knowledge.index import (
    MAX_FILE_BYTES,
    Document,
    IndexStats,
    KnowledgeIndex,
    SearchHit,
    document_from_bytes,
)
from panos_core.observability import get_logger
from panos_core.repository.walk import iter_project_files

log = get_logger(__name__)

EMBED_BATCH = 32


class Embedder(Protocol):
    """Port to the AI gateway. Embeddings follow the content's privacy label, so local-only
    chunks can only ever be embedded by a local model (ADR-008)."""

    async def __call__(
        self, texts: Sequence[str], labels: Sequence[PrivacyMode], *, query: bool
    ) -> tuple[list[list[float]], str]: ...


def _collect(project: Project, known: dict[str, str]) -> tuple[list[Document], set[str], int]:
    root = Path(project.path)
    changed: list[Document] = []
    seen: set[str] = set()
    for f in iter_project_files(root):
        if f.size > (MAX_DOCUMENT_BYTES if extractable(f.rel) else MAX_FILE_BYTES):
            continue
        try:
            data = f.path.read_bytes()
        except OSError:
            continue
        digest = hashlib.sha256(data).hexdigest()
        doc = document_from_bytes(f.rel, data)
        if doc is None:
            continue
        seen.add(f.rel)
        if known.get(f.rel) != digest:
            changed.append(doc)
    return changed, seen, len(seen)


class KnowledgeService:
    def __init__(self, index: KnowledgeIndex, audit: AuditService) -> None:
        self.index = index
        self._audit = audit
        self._embedder: Embedder | None = None
        self.embedding_model: str | None = None

    def attach_embedder(self, embedder: Embedder) -> None:
        self._embedder = embedder

    async def index_project(
        self, project: Project, *, actor: str = "system", semantic: bool = False
    ) -> IndexStats:
        if not project.knowledge_enabled:
            return IndexStats(0, 0, 0, 0, 0)
        known = await asyncio.to_thread(self.index.known_hashes, project.id)
        changed, seen, total = await asyncio.to_thread(_collect, project, known)
        history = await history_document(Path(project.path))
        if history is not None:
            seen.add(history.uri)
            total += 1
            if known.get(history.uri) != history.content_hash:
                changed.append(history)
        indexed, removed, chunks = await asyncio.to_thread(
            self.index.update, project.id, str(project.privacy_mode), changed, seen
        )
        embedded = await self.embed_project(project) if semantic else 0
        stats = IndexStats(
            files_seen=total,
            indexed=indexed,
            unchanged=total - indexed,
            removed=removed,
            chunks=chunks,
        )
        await self._audit.record(
            actor=actor,
            event_type="knowledge.indexed",
            action="knowledge.index",
            project_id=project.id,
            metadata={**stats.__dict__, "embedded": embedded},
        )
        return stats

    async def embed_project(self, project: Project) -> int:
        """Embed chunks that have no vector yet (incremental). Returns chunks embedded."""
        if self._embedder is None:
            return 0
        done = 0
        model = await self._model_name(project.privacy_mode)
        while True:
            pending = await asyncio.to_thread(
                self.index.pending_embeddings, project.id, model, EMBED_BATCH
            )
            if not pending:
                return done
            labels = [project.privacy_mode] * len(pending)
            vectors, model = await self._embedder(
                [text for _, text in pending], labels, query=False
            )
            await asyncio.to_thread(
                self.index.store_embeddings,
                model,
                [(cid, vec) for (cid, _), vec in zip(pending, vectors, strict=True)],
            )
            self.embedding_model = model
            done += len(pending)

    async def _model_name(self, privacy: PrivacyMode) -> str:
        if self.embedding_model is None and self._embedder is not None:
            _, self.embedding_model = await self._embedder(["probe"], [privacy], query=True)
        return self.embedding_model or ""

    async def search(
        self,
        query: str,
        *,
        project_id: str | None = None,
        limit: int = 20,
        privacy: PrivacyMode = PrivacyMode.LOCAL_ONLY,
    ) -> list[SearchHit]:
        """Hybrid search when the project has embeddings, lexical otherwise."""
        if self._embedder is not None and project_id is not None:
            try:
                model = await self._model_name(privacy)  # cached after the first call
            except Exception:
                model = ""  # embedding model unavailable: lexical search still works
            count = (
                await asyncio.to_thread(self.index.embedded_count, project_id, model)
                if model
                else 0
            )
            vectors: list[list[float]] = []
            if count:
                try:
                    vectors, model = await self._embedder([query], [privacy], query=True)
                except Exception:
                    log.warning("query_embedding_failed", project_id=project_id)
            if vectors:
                return await asyncio.to_thread(
                    self.index.hybrid_search,
                    query,
                    vectors[0],
                    model=model,
                    project_id=project_id,
                    limit=limit,
                )
        return await asyncio.to_thread(self.index.search, query, project_id=project_id, limit=limit)
