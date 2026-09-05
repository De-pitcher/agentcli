"""Monorepo Mesh & Multi-Repository Orchestration Package (Phase 25).

Provides multi-root workspace discovery, inter-project dependency DAG graphs,
topological build ordering, change impact analysis, and cross-repo semantic search.
"""

from .graph import DependencyCycleError, ProjectDependencyGraph
from .registry import WorkspaceRegistry, WorkspaceRoot, WorkspaceType
from .search import MultiRepoIndex, MultiRepoSearchResult

__all__ = [
    "DependencyCycleError",
    "MultiRepoIndex",
    "MultiRepoSearchResult",
    "ProjectDependencyGraph",
    "WorkspaceRegistry",
    "WorkspaceRoot",
    "WorkspaceType",
]
