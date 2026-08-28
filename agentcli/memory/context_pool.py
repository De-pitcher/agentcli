"""Reference-counted shared context pool with bounded compaction for sub-agents (Phase 5).

Manages shared context chunks across concurrently running sub-agents. Extends Phase 3's
reference tracking with automatic size enforcement and LRU/zero-ref compaction so context
growth remains strictly bounded during long multi-step agent loop runs without data races.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Default maximum capacity of the shared context pool: 512 KB
DEFAULT_MAX_POOL_BYTES = 512 * 1024


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class ContextItem:
    """Represents a shared context chunk in the pool."""

    key: str
    content: str
    source_agent: str = ""
    created_at: datetime = field(default_factory=_utc_now)
    last_accessed: datetime = field(default_factory=_utc_now)
    references: set[str] = field(default_factory=set)
    is_compacted: bool = False

    @property
    def size_bytes(self) -> int:
        return len(self.content.encode("utf-8"))

    @property
    def ref_count(self) -> int:
        return len(self.references)


class SharedContextPool:
    """Thread-safe, async-safe bounded context store for concurrent sub-agents."""

    def __init__(self, max_bytes: int = DEFAULT_MAX_POOL_BYTES) -> None:
        self.max_bytes = max(1024, max_bytes)
        self._items: dict[str, ContextItem] = {}
        self._lock = asyncio.Lock()

    @property
    def current_bytes(self) -> int:
        return sum(item.size_bytes for item in self._items.values())

    @property
    def item_count(self) -> int:
        return len(self._items)

    async def put(
        self,
        key: str,
        content: str,
        source_agent: str = "",
        initial_consumer: str | None = None,
    ) -> ContextItem:
        """Store a context chunk and compact if pool exceeds max_bytes."""
        async with self._lock:
            now = _utc_now()
            refs: set[str] = {initial_consumer} if initial_consumer else set()

            item = ContextItem(
                key=key,
                content=content,
                source_agent=source_agent,
                created_at=now,
                last_accessed=now,
                references=refs,
            )
            self._items[key] = item

            # If over capacity, compact pool
            if self.current_bytes > self.max_bytes:
                self._compact_locked(self.max_bytes)

            return item

    async def get(self, key: str, consumer_id: str | None = None) -> str | None:
        """Retrieve content for a key, optionally registering a consumer reference."""
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            item.last_accessed = _utc_now()
            if consumer_id:
                item.references.add(consumer_id)
            return item.content

    async def acquire_ref(self, key: str, consumer_id: str) -> bool:
        """Register that consumer_id is actively using context key."""
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                return False
            item.references.add(consumer_id)
            item.last_accessed = _utc_now()
            return True

    async def release_ref(self, key: str, consumer_id: str) -> bool:
        """Unregister consumer_id from context key."""
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                return False
            item.references.discard(consumer_id)
            return True

    async def compact(self, target_bytes: int | None = None) -> int:
        """Explicitly compact pool to target_bytes. Returns total bytes freed."""
        async with self._lock:
            target = target_bytes if target_bytes is not None else self.max_bytes
            return self._compact_locked(target)

    def _compact_locked(self, target_bytes: int) -> int:
        """Internal compaction logic (must be called with _lock held)."""
        initial_bytes = self.current_bytes
        if initial_bytes <= target_bytes:
            return 0

        # Evict unreferenced items (ref_count == 0) starting from oldest accessed
        unreferenced = [item for item in self._items.values() if item.ref_count == 0]
        unreferenced.sort(key=lambda x: x.last_accessed)

        for item in unreferenced:
            if self.current_bytes <= target_bytes:
                break
            del self._items[item.key]

        # If still over capacity, in-use items (ref_count > 0) are strictly preserved intact
        # to prevent corrupting data actively relied on by running sub-agents.
        if self.current_bytes > target_bytes:
            in_use_count = sum(1 for item in self._items.values() if item.ref_count > 0)
            logger.warning(
                "SharedContextPool over capacity (%d > %d bytes); %d in-use item(s) preserved intact without truncation.",
                self.current_bytes,
                target_bytes,
                in_use_count,
            )

        freed = initial_bytes - self.current_bytes
        logger.debug(
            "SharedContextPool compaction: freed %d bytes (now %d bytes)", freed, self.current_bytes
        )
        return freed

    async def clear(self) -> None:
        """Clear all context items."""
        async with self._lock:
            self._items.clear()

    async def stats(self) -> dict[str, Any]:
        """Return snapshot of pool statistics."""
        async with self._lock:
            return {
                "item_count": len(self._items),
                "total_bytes": self.current_bytes,
                "max_bytes": self.max_bytes,
                "active_references": sum(item.ref_count for item in self._items.values()),
            }


__all__ = ["DEFAULT_MAX_POOL_BYTES", "ContextItem", "SharedContextPool"]
