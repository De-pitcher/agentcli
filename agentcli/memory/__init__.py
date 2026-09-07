"""agentcli memory package — conversation persistence, context caching, budgeting, and adaptive governor (Phase 35)."""

from __future__ import annotations

from .adaptive_compressor import (
    AdaptiveContextCompressor,
    CompressionMetrics,
    collapse_repeated_lines,
    strip_ansi_codes,
)
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
from .governor import (
    BudgetHealth,
    TokenBudgetGovernor,
    UsageRecord,
)
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
    "AdaptiveContextCompressor",
    "BudgetHealth",
    "CachedFileContext",
    "CompressionMetrics",
    "ContextCache",
    "ContextItem",
    "MemoryStore",
    "MessageRecord",
    "SessionRecord",
    "SharedContextPool",
    "TokenBudgetGovernor",
    "UsageRecord",
    "collapse_repeated_lines",
    "default_memory_db_path",
    "estimate_history_tokens",
    "estimate_message_tokens",
    "estimate_tokens",
    "get_default_context_cache",
    "strip_ansi_codes",
    "trim_history_to_budget",
]
