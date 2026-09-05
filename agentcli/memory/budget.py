"""Token estimation and context-window budgeting (Phase 5).

Reconciles turn-based capping with dynamic per-model token budgeting.
Estimates token counts using lightweight zero-dependency character heuristics
(~3.8–4 chars per token) and trims conversation history to fit safely within
the target model's context window.
"""

from __future__ import annotations

import logging

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


def prune_tool_output(content: str, max_chars: int = 1000) -> str:
    """Tier 1: Truncate large tool output preserving head and tail excerpts.

    Args:
        content: Raw output string (e.g. large file contents or command stdout).
        max_chars: Maximum character limit before truncation is applied.

    Returns:
        Pruned string with omitted character count notice.
    """
    if not content or len(content) <= max_chars:
        return content

    half = max_chars // 2
    omitted = len(content) - max_chars
    return (
        f"{content[:half]}\n"
        f"\n[... {omitted:,} characters omitted for context window budget ...]\n\n"
        f"{content[-half:]}"
    )


def compress_history_tier1(
    messages: list[ChatMessage],
    max_tool_chars: int = 1000,
) -> list[ChatMessage]:
    """Tier 1 Compression: Prune oversized tool and assistant response outputs."""
    compressed: list[ChatMessage] = []
    for msg in messages:
        if msg.role in ("tool", "assistant") and len(msg.content or "") > max_tool_chars:
            compressed.append(
                ChatMessage(
                    role=msg.role,
                    content=prune_tool_output(msg.content or "", max_chars=max_tool_chars),
                )
            )
        else:
            compressed.append(msg)
    return compressed


def compress_history_tier2(
    messages: list[ChatMessage],
    keep_recent_turns: int = 3,
) -> list[ChatMessage]:
    """Tier 2 Compression: Synthesize older turn pairs into a condensed fact summary."""
    if not messages:
        return []

    system_msg: ChatMessage | None = None
    chat_msgs = list(messages)

    if chat_msgs and chat_msgs[0].role == "system":
        system_msg = chat_msgs.pop(0)

    recent_msg_count = keep_recent_turns * 2
    if len(chat_msgs) <= recent_msg_count:
        if system_msg is not None:
            return [system_msg, *chat_msgs]
        return chat_msgs

    older_msgs = chat_msgs[:-recent_msg_count]
    recent_msgs = chat_msgs[-recent_msg_count:]

    # Synthesize older messages
    summaries: list[str] = []
    for i, m in enumerate(older_msgs):
        prefix = f"[{m.role.upper()}]"
        preview = (m.content or "").strip().replace("\n", " ")
        if len(preview) > 120:
            preview = f"{preview[:120]}..."
        summaries.append(f"{prefix} {preview}")

    summary_text = (
        "[Previous Conversation Summary]\n"
        + "\n".join(f"- {s}" for s in summaries)
        + "\n[End of Summary]"
    )
    summary_msg = ChatMessage(role="user", content=summary_text)

    result: list[ChatMessage] = []
    if system_msg is not None:
        result.append(system_msg)
    result.append(summary_msg)
    result.extend(recent_msgs)
    return result


def emergency_context_reset(
    messages: list[ChatMessage],
    user_goal: str = "",
    touched_files: list[str] | None = None,
) -> list[ChatMessage]:
    """Tier 3 Compression: Extreme context reset preserving system prompt, goal, and files."""
    system_msg: ChatMessage | None = None
    for msg in messages:
        if msg.role == "system":
            system_msg = msg
            break

    files_clause = ""
    if touched_files:
        files_clause = f"\nTouched Workspace Files: {', '.join(touched_files)}"

    reset_notice = (
        f"[Emergency Context Budget Reset]\n"
        f"Active Goal: {user_goal or 'Continue executing current task'}{files_clause}\n"
        f"Please proceed with the next step."
    )

    last_user_or_tool_msg = messages[-1] if messages else ChatMessage(role="user", content=user_goal)
    result: list[ChatMessage] = []
    if system_msg is not None:
        result.append(system_msg)
    result.append(ChatMessage(role="user", content=reset_notice))
    if last_user_or_tool_msg != system_msg and last_user_or_tool_msg.content != reset_notice:
        result.append(last_user_or_tool_msg)

    return result


def adaptive_budget_compress(
    history: list[ChatMessage],
    max_context_tokens: int,
    user_goal: str = "",
    touched_files: list[str] | None = None,
    budget_ratio: float = DEFAULT_BUDGET_RATIO,
) -> list[ChatMessage]:
    """Progressively apply Tier 1 -> Tier 2 -> Tier 3 compression until history fits budget."""
    target_tokens = int(max_context_tokens * budget_ratio)
    current_tokens = estimate_history_tokens(history)

    if current_tokens <= target_tokens:
        return history

    # Pass 1: Prune oversized tool/assistant outputs
    t1_history = compress_history_tier1(history, max_tool_chars=1200)
    if estimate_history_tokens(t1_history) <= target_tokens:
        return t1_history

    # Pass 2: Synthesize older turn history
    t2_history = compress_history_tier2(t1_history, keep_recent_turns=3)
    if estimate_history_tokens(t2_history) <= target_tokens:
        return t2_history

    # Pass 3: Tighter Tier 2 with only 1 recent turn
    t2_tight = compress_history_tier2(t1_history, keep_recent_turns=1)
    if estimate_history_tokens(t2_tight) <= target_tokens:
        return t2_tight

    # Pass 4: Tier 3 Emergency Context Reset
    return emergency_context_reset(
        messages=history,
        user_goal=user_goal,
        touched_files=touched_files,
    )


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
    "adaptive_budget_compress",
    "calculate_cost",
    "compress_history_tier1",
    "compress_history_tier2",
    "emergency_context_reset",
    "estimate_history_tokens",
    "estimate_message_tokens",
    "estimate_tokens",
    "prune_tool_output",
    "trim_history_to_budget",
]
