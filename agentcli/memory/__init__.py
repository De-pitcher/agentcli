"""agentcli memory package — conversation persistence, context caching, and budgeting (Phase 5)."""

from __future__ import annotations

from .budget import (
    CHARS_PER_TOKEN,
    DEFAULT_BUDGET_RATIO,
    DEFAULT_CONTEXT_WINDOW,
    estimate_history_tokens,
    estimate_message_tokens,
    estimate_tokens,
    trim_history_to_budget,
)
from .cache import CachedFileContext, ContextCache, get_default_context_cache
from .context_pool import ContextItem, SharedContextPool
from .store import (
    MemoryStore,
    MessageRecord,
    SessionRecord,
    default_memory_db_path,
)

__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_BUDGET_RATIO",
    "DEFAULT_CONTEXT_WINDOW",
    "CachedFileContext",
    "ContextCache",
    "ContextItem",
    "MemoryStore",
    "MessageRecord",
    "SessionRecord",
    "SharedContextPool",
    "default_memory_db_path",
    "estimate_history_tokens",
    "estimate_message_tokens",
    "estimate_tokens",
    "get_default_context_cache",
    "trim_history_to_budget",
]
