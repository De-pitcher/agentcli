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
    return estimate_tokens(message.content or "") + 4


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


# Pricing rates in USD per 1M tokens: (prompt_rate, completion_rate)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Free models
    "google/gemma-4-31b-it:free": (0.0, 0.0),
    "cohere/north-mini-code:free": (0.0, 0.0),
    "z-ai/glm-5.2:free": (0.0, 0.0),
    "nvidia/nemotron-3-super-120b-a12b:free": (0.0, 0.0),
    "minimax/minimax-m2.7:free": (0.0, 0.0),
    "poolside/laguna-s-2.1:free": (0.0, 0.0),
    "nvidia/nemotron-3-ultra-550b-a55b:free": (0.0, 0.0),
    "minimax/minimax-m3:free": (0.0, 0.0),
    "thinkingmachines/inkling-small:free": (0.0, 0.0),
    "google/gemma-4-26b-a4b-it:free": (0.0, 0.0),
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": (0.0, 0.0),
    "dots-studio/dots-3-note-preview:free": (0.0, 0.0),
    "nvidia/nemotron-3.5-lightning:free": (0.0, 0.0),
    "liquid/lfm-2.5-2.6b:free": (0.0, 0.0),
    # Medium tier
    "openai/gpt-4o-mini": (0.15, 0.60),
    "anthropic/claude-3.5-haiku": (0.80, 4.00),
    "deepseek/deepseek-chat": (0.14, 0.28),
    "meta-llama/llama-3.3-70b-instruct": (0.40, 0.40),
    "qwen/qwen-2.5-coder-32b-instruct": (0.20, 0.20),
    # High tier
    "anthropic/claude-3.5-sonnet": (3.00, 15.00),
    "deepseek/deepseek-r1": (0.55, 2.19),
    "openai/gpt-4o": (2.50, 10.00),
    "google/gemini-2.5-pro": (1.25, 5.00),
}

DEFAULT_PAID_RATES: tuple[float, float] = (1.00, 3.00)


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate the estimated USD cost of an API call given model and token counts."""
    if not model or ":free" in model:
        return 0.0
    prompt_rate, completion_rate = MODEL_PRICING.get(model, DEFAULT_PAID_RATES)
    cost = (prompt_tokens * prompt_rate / 1_000_000.0) + (
        completion_tokens * completion_rate / 1_000_000.0
    )
    return round(cost, 6)


__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_BUDGET_RATIO",
    "DEFAULT_CONTEXT_WINDOW",
    "DEFAULT_PAID_RATES",
    "MODEL_PRICING",
    "calculate_cost",
    "estimate_history_tokens",
    "estimate_message_tokens",
    "estimate_tokens",
    "trim_history_to_budget",
]
