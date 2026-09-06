"""Test suite for multi-tier context compression and emergency reset (Phase 29)."""

from __future__ import annotations

from agentcli.memory.budget import (
    adaptive_budget_compress,
    compress_history_tier1,
    compress_history_tier2,
    emergency_context_reset,
    estimate_history_tokens,
    prune_tool_output,
)
from agentcli.openrouter_client import ChatMessage


def test_prune_tool_output_length_threshold() -> None:
    """Test prune_tool_output preserves head/tail and reports omitted count."""
    short = "Short output 123"
    assert prune_tool_output(short, max_chars=50) == short

    long_output = "HEAD_" + ("x" * 2000) + "_TAIL"
    pruned = prune_tool_output(long_output, max_chars=100)
    assert len(pruned) < len(long_output)
    assert "HEAD_" in pruned
    assert "_TAIL" in pruned
    assert "characters omitted for context window budget" in pruned


def test_compress_history_tier1_tool_pruning() -> None:
    """Test Tier 1 compression specifically targets tool and assistant outputs."""
    messages = [
        ChatMessage(role="system", content="System instruction"),
        ChatMessage(role="user", content="User prompt"),
        ChatMessage(role="tool", content="A" * 5000),
        ChatMessage(role="assistant", content="Normal short answer"),
    ]

    compressed = compress_history_tier1(messages, max_tool_chars=500)
    assert len(compressed) == 4
    assert compressed[0].content == "System instruction"
    assert compressed[1].content == "User prompt"
    assert len(compressed[2].content or "") < 1000
    assert "omitted" in (compressed[2].content or "")
    assert compressed[3].content == "Normal short answer"


def test_compress_history_tier2_turn_synthesis() -> None:
    """Test Tier 2 compression condenses older turns into a summary block."""
    messages = [
        ChatMessage(role="system", content="System prompt"),
        ChatMessage(role="user", content="Turn 1 user message"),
        ChatMessage(role="assistant", content="Turn 1 response"),
        ChatMessage(role="user", content="Turn 2 user message"),
        ChatMessage(role="assistant", content="Turn 2 response"),
        ChatMessage(role="user", content="Turn 3 user message"),
        ChatMessage(role="assistant", content="Turn 3 response"),
        ChatMessage(role="user", content="Turn 4 user message"),
        ChatMessage(role="assistant", content="Turn 4 response"),
    ]

    compressed = compress_history_tier2(messages, keep_recent_turns=2)
    # Result should be: System + [Previous Conversation Summary] + Turn 3 pair + Turn 4 pair
    assert len(compressed) == 6
    assert compressed[0].role == "system"
    assert "[Previous Conversation Summary]" in (compressed[1].content or "")
    assert "Turn 1 user message" in (compressed[1].content or "")
    assert compressed[-1].content == "Turn 4 response"


def test_emergency_context_reset_preserves_root_context() -> None:
    """Test Tier 3 emergency reset retains system prompt and active goal/files."""
    messages = [
        ChatMessage(role="system", content="Act as an autonomous coding agent."),
        ChatMessage(role="user", content="Fix the bug in auth.py"),
        ChatMessage(role="assistant", content="Examining files..."),
        ChatMessage(role="tool", content="Stack trace lines..."),
    ]

    reset_msgs = emergency_context_reset(
        messages=messages,
        user_goal="Fix the bug in auth.py",
        touched_files=["src/auth.py", "tests/test_auth.py"],
    )

    assert len(reset_msgs) >= 2
    assert reset_msgs[0].role == "system"
    assert "Emergency Context Budget Reset" in (reset_msgs[1].content or "")
    assert "Touched Workspace Files: src/auth.py, tests/test_auth.py" in (reset_msgs[1].content or "")


def test_adaptive_budget_compress_tiered_progression() -> None:
    """Test adaptive compression applies progressive tiers until budget is satisfied."""
    # Build large history
    large_messages = [
        ChatMessage(role="system", content="You are an expert coder."),
        ChatMessage(role="user", content="Initial goal"),
    ]
    for i in range(10):
        large_messages.append(ChatMessage(role="assistant", content=f"Step {i}: " + ("x" * 1000)))
        large_messages.append(ChatMessage(role="tool", content=f"Output {i}: " + ("y" * 2000)))

    # Force tight token budget
    compressed = adaptive_budget_compress(
        history=large_messages,
        max_context_tokens=1000,
        user_goal="Refactor auth",
        budget_ratio=0.5,
    )

    estimated_tokens = estimate_history_tokens(compressed)
    assert estimated_tokens <= 1000
