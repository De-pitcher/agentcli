"""Adaptive Context Compression and Tool Output Condensation (Phase 35).

Progressively compresses tool stdout, large file contents, and older conversation
turns to maximize effective context window utilization without losing critical reasoning.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

from ..openrouter_client import ChatMessage
from .budget import (
    DEFAULT_BUDGET_RATIO,
    estimate_history_tokens,
)

logger = logging.getLogger(__name__)

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi_codes(text: str) -> str:
    """Remove ANSI escape sequences and terminal formatting codes."""
    return ANSI_ESCAPE_RE.sub("", text)


def collapse_repeated_lines(text: str, max_consecutive: int = 2) -> str:
    """Collapse sequences of identical repeating lines in logs/stdout."""
    lines = text.splitlines()
    if len(lines) <= max_consecutive:
        return text

    collapsed: list[str] = []
    prev_line: str | None = None
    repeat_count = 0

    for line in lines:
        if line == prev_line:
            repeat_count += 1
            if repeat_count < max_consecutive:
                collapsed.append(line)
        else:
            if repeat_count >= max_consecutive:
                omitted = repeat_count - max_consecutive + 1
                collapsed.append(f"  [... line repeated {omitted} more times ...]")
            prev_line = line
            repeat_count = 0
            collapsed.append(line)

    if repeat_count >= max_consecutive:
        omitted = repeat_count - max_consecutive + 1
        collapsed.append(f"  [... line repeated {omitted} more times ...]")

    return "\n".join(collapsed)


@dataclass
class CompressionMetrics:
    """Statistics for context compression runs."""

    runs_count: int = 0
    original_tokens: int = 0
    compressed_tokens: int = 0

    @property
    def tokens_saved(self) -> int:
        return max(0, self.original_tokens - self.compressed_tokens)

    @property
    def compression_ratio(self) -> float:
        if self.original_tokens == 0:
            return 1.0
        return round(self.compressed_tokens / self.original_tokens, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runs_count": self.runs_count,
            "original_tokens": self.original_tokens,
            "compressed_tokens": self.compressed_tokens,
            "tokens_saved": self.tokens_saved,
            "compression_ratio": self.compression_ratio,
        }


class AdaptiveContextCompressor:
    """Multi-tiered context compression engine with output deduplication."""

    def __init__(
        self,
        target_budget_ratio: float = DEFAULT_BUDGET_RATIO,
        max_tool_chars: int = 1200,
        keep_recent_turns: int = 3,
    ) -> None:
        self.target_budget_ratio = target_budget_ratio
        self.max_tool_chars = max_tool_chars
        self.keep_recent_turns = keep_recent_turns
        self.metrics = CompressionMetrics()
        self._seen_outputs: dict[str, str] = {}

    def compress_tool_output(self, content: str, max_chars: int | None = None) -> str:
        """Condense large tool or command output while preserving head and tail context.

        1. Strips ANSI escape color codes.
        2. Collapses identical repeating lines.
        3. Applies head/tail truncation if exceeding max_chars.
        """
        if not content:
            return ""

        limit = max_chars or self.max_tool_chars
        cleaned = strip_ansi_codes(content)
        collapsed = collapse_repeated_lines(cleaned)

        if len(collapsed) <= limit:
            return collapsed

        # Deduplication cache check for identical huge outputs
        content_hash = hashlib.sha256(collapsed.encode("utf-8")).hexdigest()[:12]
        if content_hash in self._seen_outputs and len(collapsed) > limit * 2:
            return f"[Tool Output: Identical to previous output (hash: {content_hash}, {len(collapsed):,} chars)]"
        self._seen_outputs[content_hash] = collapsed[:100]

        half = limit // 2
        omitted = len(collapsed) - limit
        return (
            f"{collapsed[:half]}\n"
            f"\n[... {omitted:,} characters omitted by context governor ...]\n\n"
            f"{collapsed[-half:]}"
        )

    def compress_tier1(self, history: list[ChatMessage]) -> list[ChatMessage]:
        """Tier 1: Prune oversized tool and assistant response outputs."""
        compressed: list[ChatMessage] = []
        for msg in history:
            if msg.role in ("tool", "assistant") and len(msg.content or "") > self.max_tool_chars:
                compressed.append(
                    ChatMessage(
                        role=msg.role,
                        content=self.compress_tool_output(msg.content or ""),
                    )
                )
            else:
                compressed.append(msg)
        return compressed

    def compress_tier2(
        self,
        history: list[ChatMessage],
        keep_recent: int | None = None,
    ) -> list[ChatMessage]:
        """Tier 2: Synthesize older turn history into structured milestone summaries."""
        if not history:
            return []

        recent_turns = keep_recent if keep_recent is not None else self.keep_recent_turns
        system_msg: ChatMessage | None = None
        chat_msgs = list(history)

        if chat_msgs and chat_msgs[0].role == "system":
            system_msg = chat_msgs.pop(0)

        recent_msg_count = recent_turns * 2
        if len(chat_msgs) <= recent_msg_count:
            if system_msg is not None:
                return [system_msg, *chat_msgs]
            return chat_msgs

        older_msgs = chat_msgs[:-recent_msg_count]
        recent_msgs = chat_msgs[-recent_msg_count:]

        summaries: list[str] = []
        for m in older_msgs:
            prefix = f"[{m.role.upper()}]"
            preview = (m.content or "").strip().replace("\n", " ")
            if len(preview) > 140:
                preview = f"{preview[:140]}..."
            summaries.append(f"{prefix} {preview}")

        summary_text = (
            "[Previous Milestone Context Summary]\n"
            + "\n".join(f"- {s}" for s in summaries)
            + "\n[End of Milestone Context]"
        )
        summary_msg = ChatMessage(role="user", content=summary_text)

        result: list[ChatMessage] = []
        if system_msg is not None:
            result.append(system_msg)
        result.append(summary_msg)
        result.extend(recent_msgs)
        return result

    def compress_tier3_emergency(
        self,
        history: list[ChatMessage],
        user_goal: str = "",
        touched_files: list[str] | None = None,
    ) -> list[ChatMessage]:
        """Tier 3: Emergency distillation preserving system instructions, goal, and files."""
        system_msg: ChatMessage | None = None
        for msg in history:
            if msg.role == "system":
                system_msg = msg
                break

        files_clause = ""
        if touched_files:
            files_clause = f"\nTouched Workspace Files: {', '.join(touched_files)}"

        reset_notice = (
            f"[Emergency Context Budget Reset]\n"
            f"Active Goal: {user_goal or 'Continue executing task'}{files_clause}\n"
            f"Proceeding with latest operational state."
        )

        last_user_or_tool = (
            history[-1] if history else ChatMessage(role="user", content=user_goal)
        )
        result: list[ChatMessage] = []
        if system_msg is not None:
            result.append(system_msg)
        result.append(ChatMessage(role="user", content=reset_notice))
        if last_user_or_tool != system_msg and last_user_or_tool.content != reset_notice:
            result.append(last_user_or_tool)

        return result

    def compress(
        self,
        history: list[ChatMessage],
        max_context_tokens: int,
        user_goal: str = "",
        touched_files: list[str] | None = None,
    ) -> list[ChatMessage]:
        """Progressively apply compression tiers until history fits the target token budget."""
        if not history:
            return []

        orig_tok = estimate_history_tokens(history)
        target_tokens = max(16, int(max_context_tokens * self.target_budget_ratio))

        if orig_tok <= target_tokens:
            return history

        # Pass 1: Prune large tool outputs
        t1 = self.compress_tier1(history)
        t1_tok = estimate_history_tokens(t1)
        if t1_tok <= target_tokens:
            self._record_metrics(orig_tok, t1_tok)
            return t1

        # Pass 2: Synthesize older turns
        t2 = self.compress_tier2(t1, keep_recent=self.keep_recent_turns)
        t2_tok = estimate_history_tokens(t2)
        if t2_tok <= target_tokens:
            self._record_metrics(orig_tok, t2_tok)
            return t2

        # Pass 3: Tighter Tier 2 with only 1 recent turn
        t2_tight = self.compress_tier2(t1, keep_recent=1)
        t2_tight_tok = estimate_history_tokens(t2_tight)
        if t2_tight_tok <= target_tokens:
            self._record_metrics(orig_tok, t2_tight_tok)
            return t2_tight

        # Pass 4: Tier 3 Emergency Context Reset
        t3 = self.compress_tier3_emergency(
            history=history,
            user_goal=user_goal,
            touched_files=touched_files,
        )
        t3_tok = estimate_history_tokens(t3)
        self._record_metrics(orig_tok, t3_tok)
        return t3

    def _record_metrics(self, original_tokens: int, compressed_tokens: int) -> None:
        self.metrics.runs_count += 1
        self.metrics.original_tokens += original_tokens
        self.metrics.compressed_tokens += compressed_tokens


__all__ = [
    "AdaptiveContextCompressor",
    "CompressionMetrics",
    "collapse_repeated_lines",
    "strip_ansi_codes",
]
