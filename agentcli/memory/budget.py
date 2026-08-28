"""Token estimation and context-window budgeting (Phase 5).

Reconciles turn-based capping with dynamic per-model token budgeting.
Estimates token counts using lightweight zero-dependency character heuristics
(~3.8–4 chars per token) and trims conversation history to fit safely within
the target model's context window.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..openrouter_client import ChatMessage

logger = logging.getLogger(__name__)

# Default fallback context window when model metadata is unavailable
DEFAULT_CONTEXT_WINDOW = 32_768
# Reserve 25% of the context window for system prompts and model generation
DEFAULT_BUDGET_RATIO = 0.75
# Character-to-token ratio approximation (~3.8 chars per token)
CHARS_PER_TOKEN = 3.8


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string using character heuristics."""
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def estimate_message_tokens(message: ChatMessage) -> int:
    """Estimate token count for a single ChatMessage including role overhead."""
    # 4 tokens overhead per message for role/formatting structure in standard chat templates
    return estimate_tokens(message.content) + 4


def estimate_history_tokens(messages: list[ChatMessage]) -> int:
    """Estimate total tokens across a list of ChatMessages."""
    return sum(estimate_message_tokens(m) for m in messages)


def trim_history_to_budget(
    history: list[ChatMessage],
    max_context_tokens: int,
    max_turns: int | None = None,
    budget_ratio: float = DEFAULT_BUDGET_RATIO,
) -> list[ChatMessage]:
    """Trim conversation history to fit within a model's token budget and turn cap.

    Strategy:
      1. Always preserve the system message at history[0] if present.
      2. Deduct system message tokens from the available budget.
      3. Walk backward from the most recent messages, adding turns until the
         token budget or max_turns cap is reached.
      4. Return the resulting messages in chronological order.

    Args:
        history: Complete list of ChatMessages in session.
        max_context_tokens: Total context window size of the active model.
        max_turns: Optional upper bound on turn pairs (fallback from history_turns).
        budget_ratio: Fraction of max_context_tokens to allocate for history (default: 0.75).

    Returns:
        Pruned list of ChatMessages satisfying token and turn constraints.
    """
    if not history:
        return []

    target_token_budget = max(512, int(max_context_tokens * budget_ratio))

    system_msg: ChatMessage | None = None
    chat_msgs = list(history)

    if chat_msgs and chat_msgs[0].role == "system":
        system_msg = chat_msgs.pop(0)
        system_tokens = estimate_message_tokens(system_msg)
        target_token_budget = max(256, target_token_budget - system_tokens)

    # If max_turns is specified, apply turn count slicing as an initial upper bound
    if max_turns is not None and max_turns > 0:
        max_msg_count = max_turns * 2 + 1
        chat_msgs = chat_msgs[-max_msg_count:]

    # Walk backwards and accumulate messages that fit within target_token_budget
    selected_reversed: list[ChatMessage] = []
    used_tokens = 0

    for msg in reversed(chat_msgs):
        msg_tokens = estimate_message_tokens(msg)
        if used_tokens + msg_tokens <= target_token_budget or not selected_reversed:
            # Always include at least the very latest message even if tight on budget
            selected_reversed.append(msg)
            used_tokens += msg_tokens
        else:
            # Reached token budget limit
            break

    selected_chronological = list(reversed(selected_reversed))

    if system_msg is not None:
        return [system_msg, *selected_chronological]
    return selected_chronological


__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_BUDGET_RATIO",
    "DEFAULT_CONTEXT_WINDOW",
    "estimate_history_tokens",
    "estimate_message_tokens",
    "estimate_tokens",
    "trim_history_to_budget",
]
