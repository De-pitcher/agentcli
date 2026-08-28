"""Context caching and invalidation layer (Phase 5).

Caches formatted file contents referenced via `@path` tokens to prevent redundant
token spend and redundant disk I/O on unchanged files. Detects changes via mtime and
SHA-256 content hashing.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CachedFileContext:
    """Represents a cached file's content, formatting, and hash."""

    path: str
    mtime: float
    sha256: str
    formatted_block: str
    char_count: int


class ContextCache:
    """In-memory cache for formatted file contexts with hash/mtime invalidation."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._entries: dict[str, CachedFileContext] = {}
        self._hits = 0
        self._misses = 0

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    def get_or_read(
        self,
        path: str | Path,
        reader_fn: Callable[[Path], str],
    ) -> tuple[str, bool]:
        """Retrieve formatted file context from cache, or read and update cache.

        Args:
            path: Path to the target file.
            reader_fn: Callback taking a Path and returning the formatted string block.

        Returns:
            Tuple of (formatted_content, is_cache_hit)
        """
        p = Path(path).resolve()
        p_str = str(p)

        if not self.enabled:
            return reader_fn(p), False

        if not p.exists() or not p.is_file():
            self.invalidate(p_str)
            return reader_fn(p), False

        try:
            current_mtime = p.stat().st_mtime
        except OSError:
            return reader_fn(p), False

        # Fast path: check mtime match
        cached = self._entries.get(p_str)
        if cached is not None and cached.mtime == current_mtime:
            self._hits += 1
            return cached.formatted_block, True

        # Compute content hash on mtime change or cache miss
        try:
            content_bytes = p.read_bytes()
            current_hash = hashlib.sha256(content_bytes).hexdigest()
        except OSError:
            return reader_fn(p), False

        if cached is not None and cached.sha256 == current_hash:
            # mtime touched but content identical: refresh mtime and return cached
            cached.mtime = current_mtime
            self._hits += 1
            return cached.formatted_block, True

        # Cache miss or file modified
        formatted = reader_fn(p)
        self._misses += 1
        self._entries[p_str] = CachedFileContext(
            path=p_str,
            mtime=current_mtime,
            sha256=current_hash,
            formatted_block=formatted,
            char_count=len(formatted),
        )
        return formatted, False

    def invalidate(self, path: str | Path) -> bool:
        """Evict a specific path from the cache."""
        p_str = str(Path(path).resolve())
        return self._entries.pop(p_str, None) is not None

    def clear(self) -> None:
        """Clear all cached file contexts and reset statistics."""
        self._entries.clear()
        self._hits = 0
        self._misses = 0

    def stats(self) -> dict[str, int]:
        """Return cache hit and miss statistics."""
        return {
            "cached_entries": len(self._entries),
            "hits": self._hits,
            "misses": self._misses,
        }


# Global default cache instance for module-level helpers
_GLOBAL_CONTEXT_CACHE = ContextCache(enabled=True)


def get_default_context_cache() -> ContextCache:
    """Return the global shared ContextCache instance."""
    return _GLOBAL_CONTEXT_CACHE


__all__ = ["CachedFileContext", "ContextCache", "get_default_context_cache"]
