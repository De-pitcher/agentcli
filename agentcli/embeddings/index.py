"""Semantic vector index and fast cosine similarity search engine (Phase 24).

Coordinates file chunking, embedding generation, SQLite caching, and in-memory
vector similarity ranking.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .chunker import CODE_EXTENSIONS, CodeChunk, chunk_file
from .engine import EmbeddingEngine
from .store import VectorStore

logger = logging.getLogger(__name__)

DEFAULT_EXCLUDE_DIRS: set[str] = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    ".agentcli_worktrees",
}


@dataclass
class SearchResult:
    """Represents a matched code chunk with similarity score."""

    chunk: CodeChunk
    score: float

    @property
    def file_path(self) -> str:
        return self.chunk.file_path

    @property
    def start_line(self) -> int:
        return self.chunk.start_line

    @property
    def end_line(self) -> int:
        return self.chunk.end_line

    @property
    def content(self) -> str:
        return self.chunk.content

    def format_block(self) -> str:
        """Format the matched code snippet with header for context injection."""
        return (
            f"--- {self.chunk.file_path}:{self.chunk.start_line}-{self.chunk.end_line} "
            f"({self.chunk.chunk_type}, similarity: {self.score:.2f}) ---\n"
            f"{self.chunk.content}\n"
        )


def _dot_product(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute dot product of two vectors."""
    return sum(a * b for a, b in zip(vec_a, vec_b, strict=False))


class VectorIndex:
    """In-memory search index backed by persistent SQLite vector store."""

    def __init__(
        self,
        store: VectorStore | None = None,
        engine: EmbeddingEngine | None = None,
        similarity_threshold: float = 0.30,
        max_results: int = 5,
    ) -> None:
        self.store = store or VectorStore()
        self.engine = engine or EmbeddingEngine()
        self.similarity_threshold = similarity_threshold
        self.max_results = max_results

    async def index_files(
        self,
        paths: Sequence[str | Path],
        force: bool = False,
    ) -> int:
        """Index a list of source files, embedding only new or modified chunks.

        Returns the number of newly embedded chunks.
        """
        all_chunks: list[CodeChunk] = []
        for p in paths:
            chunks = chunk_file(p)
            all_chunks.extend(chunks)

        if not all_chunks:
            return 0

        # Determine which chunks need embedding
        uncached_chunks: list[CodeChunk] = []
        model = self.engine.model

        for chunk in all_chunks:
            if not force:
                cached_vec = self.store.get_embedding(chunk.chunk_id, chunk.sha256, model)
                if cached_vec is not None:
                    continue
            uncached_chunks.append(chunk)

        if not uncached_chunks:
            return 0

        logger.debug(
            "VectorIndex: Generating embeddings for %d uncached chunks via %s",
            len(uncached_chunks),
            model,
        )

        texts = [c.content for c in uncached_chunks]
        vectors = await self.engine.embed_texts(texts)

        records_to_save: list[tuple[CodeChunk, str, list[float]]] = []
        for chunk, vec in zip(uncached_chunks, vectors, strict=False):
            records_to_save.append((chunk, model, vec))

        self.store.save_embeddings_batch(records_to_save)
        return len(records_to_save)

    async def index_workspace(
        self,
        root: str | Path = ".",
        exclude_dirs: set[str] | None = None,
        force: bool = False,
    ) -> int:
        """Discover and index all code files in the workspace directory tree."""
        root_path = Path(root).resolve()
        excludes = exclude_dirs or DEFAULT_EXCLUDE_DIRS
        files_to_index: list[Path] = []

        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [d for d in dirnames if d not in excludes and not d.startswith(".")]
            for f in filenames:
                p = Path(dirpath) / f
                if p.suffix.lower() in CODE_EXTENSIONS and p.stat().st_size < 500_000:
                    files_to_index.append(p)

        return await self.index_files(files_to_index, force=force)

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        threshold: float | None = None,
        file_filter: str | None = None,
    ) -> list[SearchResult]:
        """Perform semantic search against indexed code chunks using cosine similarity."""
        k = top_k if top_k is not None else self.max_results
        min_thresh = threshold if threshold is not None else self.similarity_threshold

        query_vec = await self.engine.embed_query(query)
        cached_records = self.store.get_all_for_model(self.engine.model)
        if not cached_records:
            return []

        scored: list[SearchResult] = []
        filter_lower = file_filter.lower() if file_filter else None

        for chunk, chunk_vec in cached_records:
            if filter_lower and filter_lower not in chunk.file_path.lower():
                continue

            score = _dot_product(query_vec, chunk_vec)
            if score >= min_thresh:
                scored.append(SearchResult(chunk=chunk, score=score))

        # Sort descending by similarity score
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:k]

    async def sync_file(self, file_path: str | Path) -> int:
        """Incrementally synchronize a single file: purge stale chunks and re-index new chunks."""
        p = Path(file_path).resolve()
        # If file deleted or not a code file, delete chunks
        if not p.exists() or not p.is_file():
            return self.store.delete_file_chunks(str(p))
        if p.suffix.lower() not in CODE_EXTENSIONS or p.stat().st_size >= 500_000:
            return self.store.delete_file_chunks(str(p))

        # Purge existing stale chunks for this file
        self.store.delete_file_chunks(str(p))
        return await self.index_files([p], force=False)
