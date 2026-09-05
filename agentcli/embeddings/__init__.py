"""Semantic vector search and code embeddings package (Phase 24)."""

from .chunker import CODE_EXTENSIONS, CodeChunk, chunk_file, chunk_markdown_file, chunk_python_file
from .engine import EmbeddingEngine
from .index import SearchResult, VectorIndex
from .store import VectorStore, default_vector_db_path

__all__ = [
    "CODE_EXTENSIONS",
    "CodeChunk",
    "EmbeddingEngine",
    "SearchResult",
    "VectorIndex",
    "VectorStore",
    "chunk_file",
    "chunk_markdown_file",
    "chunk_python_file",
    "default_vector_db_path",
]
