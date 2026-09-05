"""Cross-Repository Semantic Search & Multi-Project Context Index (Phase 25).

Provides unified semantic vector search across all registered workspace roots
in the monorepo mesh with repository scoping and namespaced results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..embeddings import EmbeddingEngine, SearchResult, VectorIndex, VectorStore

if TYPE_CHECKING:
    from .registry import WorkspaceRegistry

logger = logging.getLogger(__name__)


@dataclass
class MultiRepoSearchResult:
    """Represents a matched code snippet tagged with its originating workspace root."""

    workspace: str
    result: SearchResult

    @property
    def file_path(self) -> str:
        return self.result.file_path

    @property
    def score(self) -> float:
        return self.result.score

    @property
    def content(self) -> str:
        return self.result.content

    def format_block(self) -> str:
        """Format the matched code snippet with workspace attribution header."""
        return (
            f"--- [{self.workspace}] {self.result.file_path}:{self.result.start_line}-{self.result.end_line} "
            f"({self.result.chunk.chunk_type}, similarity: {self.score:.2f}) ---\n"
            f"{self.content}\n"
        )


class MultiRepoIndex:
    """Coordinates semantic indexing and querying across multiple workspace repositories."""

    def __init__(
        self,
        registry: WorkspaceRegistry,
        store: VectorStore | None = None,
        engine: EmbeddingEngine | None = None,
        similarity_threshold: float = 0.30,
        max_results: int = 5,
    ) -> None:
        self.registry = registry
        self.store = store or VectorStore()
        self.engine = engine or EmbeddingEngine()
        self.similarity_threshold = similarity_threshold
        self.max_results = max_results
        self._index = VectorIndex(
            store=self.store,
            engine=self.engine,
            similarity_threshold=self.similarity_threshold,
            max_results=self.max_results,
        )

    def _find_workspace_for_file(self, file_path: str) -> str:
        """Determine which registered workspace contains the given file."""
        resolved = Path(file_path).resolve()
        res_str = str(resolved).lower().replace("/", "\\")
        for ws in self.registry.list_workspaces():
            ws_str = str(ws.resolved_path).lower().replace("/", "\\")
            if res_str.startswith(ws_str):
                return ws.name
        return "external"

    async def index_workspace(self, name: str, force: bool = False) -> int:
        """Index a single workspace repository by name."""
        ws = self.registry.get(name)
        if not ws:
            raise KeyError(f"Workspace '{name}' not found in registry.")

        logger.debug("Indexing workspace '%s' at %s", name, ws.path)
        return await self._index.index_workspace(root=ws.resolved_path, force=force)

    async def index_all(self, force: bool = False) -> dict[str, int]:
        """Index all registered workspaces sequentially under single-worker guardrails."""
        results: dict[str, int] = {}
        for ws in self.registry.list_workspaces():
            count = await self.index_workspace(ws.name, force=force)
            results[ws.name] = count
        return results

    async def search(
        self,
        query: str,
        repo: str | None = None,
        top_k: int | None = None,
        threshold: float | None = None,
    ) -> list[MultiRepoSearchResult]:
        """Perform semantic search across all workspaces or within a scoped repository."""
        k = top_k if top_k is not None else self.max_results
        min_thresh = threshold if threshold is not None else self.similarity_threshold

        file_filter: str | None = None
        if repo:
            ws = self.registry.get(repo)
            if not ws:
                raise KeyError(f"Workspace '{repo}' not found in registry.")
            file_filter = str(ws.resolved_path)

        raw_results = await self._index.search(
            query=query,
            top_k=k * 3,  # Fetch wider candidate pool before attribution
            threshold=min_thresh,
            file_filter=file_filter,
        )

        matched: list[MultiRepoSearchResult] = []
        for res in raw_results:
            origin_ws = self._find_workspace_for_file(res.file_path)
            if origin_ws == "external":
                continue
            if repo and origin_ws != repo:
                continue
            matched.append(MultiRepoSearchResult(workspace=origin_ws, result=res))

        return matched[:k]
