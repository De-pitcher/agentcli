from __future__ import annotations

import pytest

from agentcli.config import Config
from agentcli.memory.adaptive_compressor import (
    AdaptiveContextCompressor,
    collapse_repeated_lines,
    strip_ansi_codes,
)
from agentcli.memory.governor import BudgetHealth, TokenBudgetGovernor
from agentcli.openrouter_client import ChatMessage
from agentcli.session import AgentSession


def test_governor_record_usage_and_costs() -> None:
    """Test TokenBudgetGovernor records token usage, cached tokens, and calculates USD cost."""
    governor = TokenBudgetGovernor(max_cost_usd=1.00)

    # 1. Free model should cost $0.00
    cost1 = governor.record_usage(
        model="google/gemma-4-31b-it:free",
        prompt_tokens=1000,
        completion_tokens=500,
        cached_tokens=200,
        agent_type="planner",
    )
    assert cost1 == 0.0
    assert governor.total_tokens == 1500
    assert governor.prompt_tokens == 1000
    assert governor.completion_tokens == 500
    assert governor.cached_tokens == 200
    assert governor.total_cost_usd == 0.0

    # 2. Paid model should accumulate cost
    cost2 = governor.record_usage(
        model="openai/gpt-4o",
        prompt_tokens=10_000,  # $2.50 / 1M = $0.025
        completion_tokens=2_000,  # $10.00 / 1M = $0.020 -> total $0.045
        agent_type="code_analyzer",
    )
    assert cost2 == pytest.approx(0.045, rel=1e-3)
    assert governor.total_cost_usd == pytest.approx(0.045, rel=1e-3)
    assert governor.total_tokens == 13_500
    assert len(governor.records) == 2


def test_governor_soft_warning_and_hard_ceiling() -> None:
    """Test soft warning threshold (80%) and hard budget ceiling (100%)."""
    governor = TokenBudgetGovernor(max_cost_usd=0.10, warning_ratio=0.80)

    assert governor.is_warning() is False
    assert governor.is_exceeded() is False

    # Incur cost of $0.05 (50% - OK)
    governor.record_usage(
        model="anthropic/claude-3.5-sonnet",
        prompt_tokens=10_000,  # 10k * $3.00/1M = $0.03
        completion_tokens=1_333,  # 1.33k * $15.00/1M = $0.02
    )
    assert governor.is_warning() is False
    assert governor.is_exceeded() is False
    assert 45.0 <= governor.get_utilization_pct() <= 55.0

    # Incur additional cost to cross 80% ($0.085 total)
    governor.record_usage(
        model="anthropic/claude-3.5-sonnet",
        prompt_tokens=10_000,
        completion_tokens=1_000,
    )
    assert governor.is_warning() is True
    assert governor.is_exceeded() is False

    # Incur additional cost to cross 100% ($0.12 total)
    governor.record_usage(
        model="anthropic/claude-3.5-sonnet",
        prompt_tokens=10_000,
        completion_tokens=2_000,
    )
    assert governor.is_warning() is True
    assert governor.is_exceeded() is True
    assert governor.get_utilization_pct() >= 100.0


def test_governor_token_limit_and_health() -> None:
    """Test max_tokens ceiling and check_health snapshot generation."""
    governor = TokenBudgetGovernor(max_tokens=5000, warning_ratio=0.75)

    governor.record_usage(
        model="google/gemma-4-31b-it:free",
        prompt_tokens=2000,
        completion_tokens=1000,
    )
    health = governor.check_health()
    assert isinstance(health, BudgetHealth)
    assert health.status == "ok"
    assert health.used_tokens == 3000
    assert health.max_tokens == 5000
    assert health.utilization_pct == 60.0

    # Cross 75% soft warning
    governor.record_usage(
        model="google/gemma-4-31b-it:free",
        prompt_tokens=1000,
        completion_tokens=500,
    )
    assert governor.is_warning() is True
    assert governor.check_health().status == "warning"

    # Cross 100% token limit
    governor.record_usage(
        model="google/gemma-4-31b-it:free",
        prompt_tokens=1000,
        completion_tokens=1000,
    )
    assert governor.is_exceeded() is True
    assert governor.check_health().status == "exceeded"

    # Test health dict serialization
    h_dict = health.to_dict()
    assert "used_cost_usd" in h_dict
    assert "utilization_pct" in h_dict
    assert "cost_per_hour" in h_dict


def test_governor_velocity_and_breakdowns() -> None:
    """Test spend velocity, subagent breakdown, and model breakdown."""
    governor = TokenBudgetGovernor(max_cost_usd=1.00)

    governor.record_usage(
        model="openai/gpt-4o-mini",
        prompt_tokens=5000,
        completion_tokens=1000,
        agent_type="planner",
    )
    governor.record_usage(
        model="openai/gpt-4o-mini",
        prompt_tokens=3000,
        completion_tokens=500,
        agent_type="code_analyzer",
    )
    governor.record_usage(
        model="google/gemma-4-31b-it:free",
        prompt_tokens=4000,
        completion_tokens=200,
        agent_type="planner",
    )

    # Agent breakdown
    agent_bd = governor.get_agent_breakdown()
    assert "planner" in agent_bd
    assert "code_analyzer" in agent_bd
    assert agent_bd["planner"]["calls"] == 2
    assert agent_bd["planner"]["total_tokens"] == 10_200
    assert agent_bd["code_analyzer"]["calls"] == 1

    # Model breakdown
    model_bd = governor.get_model_breakdown()
    assert "openai/gpt-4o-mini" in model_bd
    assert "google/gemma-4-31b-it:free" in model_bd
    assert model_bd["openai/gpt-4o-mini"]["calls"] == 2

    # Spend velocity calculations
    vel_cost = governor.cost_per_hour()
    vel_tok = governor.tokens_per_minute()
    assert vel_cost >= 0.0
    assert vel_tok >= 0.0

    # Format summary table
    summary_text = governor.format_summary()
    assert "Token & Cost Budget" in summary_text
    assert "By SubAgent:" in summary_text
    assert "By Model:" in summary_text


def test_governor_set_budget_and_reset() -> None:
    """Test dynamic runtime budget adjustment and reset."""
    governor = TokenBudgetGovernor(max_cost_usd=0.50, max_tokens=10000)
    governor.record_usage("openai/gpt-4o", 1000, 500)
    assert governor.total_tokens == 1500
    assert len(governor.records) == 1

    # Update budget limits
    governor.set_budget(max_cost_usd=2.00, max_tokens=50000, warning_ratio=0.85)
    assert governor.max_cost_usd == 2.00
    assert governor.max_tokens == 50000
    assert governor.warning_ratio == 0.85

    # Reset
    governor.reset()
    assert governor.total_tokens == 0
    assert governor.total_cost_usd == 0.0
    assert len(governor.records) == 0


def test_governor_estimate_and_record_text() -> None:
    """Test estimating tokens from raw strings and recording."""
    governor = TokenBudgetGovernor()
    prompt = "Review this authentication module and check for token leaks."
    completion = "The code looks solid. No leaks found."

    cost = governor.estimate_and_record_text(
        model="openai/gpt-4o-mini",
        prompt_text=prompt,
        completion_text=completion,
        cached_tokens=10,
        agent_type="reviewer",
    )
    assert cost >= 0.0
    assert governor.prompt_tokens > 0
    assert governor.completion_tokens > 0
    assert governor.cached_tokens == 10


def test_strip_ansi_and_collapse_lines() -> None:
    """Test ANSI strip and repeated line collapsing utilities."""
    # 1. ANSI strip
    colored = "\x1b[31mError:\x1b[0m File \x1b[1mnot found\x1b[0m"
    assert strip_ansi_codes(colored) == "Error: File not found"

    # 2. Collapse repeating lines
    log_text = "Checking...\n" + ("Downloading package xyz\n" * 10) + "Done."
    collapsed = collapse_repeated_lines(log_text, max_consecutive=2)
    assert "repeated 8 more times" in collapsed
    assert "Done." in collapsed


def test_adaptive_compressor_tool_output() -> None:
    """Test tool output truncation and hash deduplication."""
    compressor = AdaptiveContextCompressor(max_tool_chars=200)

    # Short output remains unchanged
    short_text = "All 10 tests passed."
    assert compressor.compress_tool_output(short_text) == short_text

    # Long output gets truncated (using unique lines so repeated line collapse doesn't shrink it first)
    unique_lines = "\n".join(f"data record row #{i} from server output stream" for i in range(40))
    long_text = f"START_BLOCK\n{unique_lines}\nEND_BLOCK"
    pruned = compressor.compress_tool_output(long_text)
    assert "START_BLOCK" in pruned
    assert "END_BLOCK" in pruned
    assert "characters omitted by context governor" in pruned
    assert len(pruned) < len(long_text)

    # Identical huge output is deduplicated
    huge_text = "UNIQUE_HUGE_STAMP_" + ("x" * 1000)
    compressor.compress_tool_output(huge_text)
    out2 = compressor.compress_tool_output(huge_text)
    assert "Identical to previous output" in out2




def test_adaptive_compressor_progressive_tiers() -> None:
    """Test progressive compression through Tier 1, Tier 2, and Tier 3 emergency."""
    compressor = AdaptiveContextCompressor(
        target_budget_ratio=0.75,
        max_tool_chars=100,
        keep_recent_turns=1,
    )

    sys_msg = ChatMessage(role="system", content="You are a developer assistant.")
    u1 = ChatMessage(role="user", content="Task 1: Read files")
    a1 = ChatMessage(role="assistant", content="Reading file content:\n" + ("data line\n" * 30))
    u2 = ChatMessage(role="user", content="Task 2: Refactor auth")
    a2 = ChatMessage(role="assistant", content="Refactored auth module successfully.")
    u3 = ChatMessage(role="user", content="Task 3: Run test suite")

    history = [sys_msg, u1, a1, u2, a2, u3]

    # Compress history to fit within small token window (e.g. 100 tokens)
    compressed = compressor.compress(
        history=history,
        max_context_tokens=150,
        user_goal="Refactor auth and run tests",
        touched_files=["auth.py", "test_auth.py"],
    )

    assert len(compressed) >= 2
    assert compressed[0].role == "system"
    # Ensure metrics were tracked
    assert compressor.metrics.runs_count >= 1
    assert compressor.metrics.tokens_saved >= 0
    assert 0.0 < compressor.metrics.compression_ratio <= 1.0

    # Emergency compression
    emergency = compressor.compress_tier3_emergency(
        history=history,
        user_goal="Critical emergency fix",
        touched_files=["main.py"],
    )
    assert emergency[0].role == "system"
    assert any("[Emergency Context Budget Reset]" in (m.content or "") for m in emergency)


@pytest.mark.asyncio
async def test_session_governor_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test AgentSession integrates with TokenBudgetGovernor for cost and limits."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = Config()
    config.routing.max_cost_usd = 0.05

    session = AgentSession(config=config, forced_model="google/gemma-4-31b-it:free")
    assert session.governor is not None
    assert session.compressor is not None
    assert session.is_budget_exceeded() is False

    # Record cost through session
    cost = session.record_cost("openai/gpt-4o-mini", prompt_tokens=1000, completion_tokens=500)
    assert cost > 0.0
    assert session.cumulative_cost_usd == session.governor.total_cost_usd

    # Trigger budget ceiling
    session.governor.set_budget(max_cost_usd=0.0001)
    assert session.is_budget_exceeded() is True

    # When budget exceeded, step returns warning message
    reply = await session.step("Should not execute")
    assert "Session cost ceiling reached" in reply

    await session.aclose()


def test_slash_completer_budget_completions() -> None:
    """Test SlashAndFileCompleter offers new /budget subcommands."""
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from agentcli.ui.prompt import SlashAndFileCompleter

    completer = SlashAndFileCompleter()
    event = CompleteEvent()

    comps = [c.text for c in completer.get_completions(Document("/budget "), event)]
    assert "status" in comps
    assert "set" in comps
    assert "max-tokens" in comps
    assert "reset" in comps
    assert "low" in comps
    assert "medium" in comps
    assert "high" in comps


def test_adaptive_compressor_edge_cases_and_metrics() -> None:
    """Test all edge cases and helper methods in AdaptiveContextCompressor."""
    from agentcli.memory.adaptive_compressor import CompressionMetrics

    # Test CompressionMetrics
    metrics = CompressionMetrics()
    assert metrics.compression_ratio == 1.0
    assert metrics.tokens_saved == 0
    d = metrics.to_dict()
    assert d["runs_count"] == 0
    assert d["compression_ratio"] == 1.0

    compressor = AdaptiveContextCompressor(keep_recent_turns=2)

    # Empty content tool output
    assert compressor.compress_tool_output("") == ""
    assert compressor.compress([], max_context_tokens=100) == []
    assert compressor.compress_tier2([]) == []
    t3_empty = compressor.compress_tier3_emergency([])
    assert len(t3_empty) >= 1
    assert "Emergency Context Budget Reset" in (t3_empty[0].content or "")

    # Small history fits budget immediately
    short_history = [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="hi"),
    ]
    res = compressor.compress(short_history, max_context_tokens=10000)
    assert res == short_history

    # compress_tier2 with short history (<= recent_msg_count) without and with system msg
    assert compressor.compress_tier2(short_history) == short_history
    sys_short = [ChatMessage(role="system", content="sys"), *short_history]
    assert compressor.compress_tier2(sys_short) == sys_short

    # compress_tier2 with long history and long message previews (>140 chars)
    long_msg = "x" * 200
    older = [ChatMessage(role="user", content=long_msg), ChatMessage(role="assistant", content=long_msg)]
    recent = [
        ChatMessage(role="user", content="recent 1"),
        ChatMessage(role="assistant", content="recent 2"),
        ChatMessage(role="user", content="recent 3"),
        ChatMessage(role="assistant", content="recent 4"),
    ]
    t2_res = compressor.compress_tier2([ChatMessage(role="system", content="sys"), *older, *recent], keep_recent=2)
    assert len(t2_res) == 1 + 1 + 4  # sys + summary + 4 recent
    assert "..." in (t2_res[1].content or "")

    # compress_tier3_emergency without system message
    t3_no_sys = compressor.compress_tier3_emergency(
        older, user_goal="solve bug", touched_files=["a.py", "b.py"]
    )
    assert len(t3_no_sys) == 2
    assert "solve bug" in (t3_no_sys[0].content or "")
    assert "a.py" in (t3_no_sys[0].content or "")


def test_governor_serialization_and_resets() -> None:
    """Test TokenBudgetGovernor serialization, format summary, and resets."""
    governor = TokenBudgetGovernor(max_cost_usd=5.0, max_tokens=100_000)
    governor.record_usage(
        model="openai/gpt-4o",
        prompt_tokens=5000,
        completion_tokens=2000,
        cached_tokens=1000,
        agent_type="planner",
    )
    governor.record_usage(
        model="google/gemma-4-31b-it:free",
        prompt_tokens=1000,
        completion_tokens=500,
        agent_type="main",
    )

    summary = governor.format_summary()
    assert "Token & Cost Budget" in summary
    assert "planner" in summary
    assert "gpt-4o" in summary

    # Reset
    governor.reset()
    assert governor.total_tokens == 0
    assert governor.total_cost_usd == 0.0
    assert len(governor.records) == 0


@pytest.mark.asyncio
async def test_cli_budget_slash_command_execution(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Test all /budget subcommand executions through run_chat."""
    import argparse
    import os
    from unittest.mock import patch

    from agentcli.cli import run_chat
    from agentcli.exit_codes import ExitCode

    inputs = [
        "/budget",
        "/budget status",
        "/budget low",
        "/budget tier high",
        "/budget tier invalid_tier",
        "/budget set $0.50",
        "/budget set invalid",
        "/budget set",
        "/budget max-tokens 50,000",
        "/budget max-tokens invalid",
        "/budget max-tokens",
        "/budget reset",
        "/cost",
        "/tokens",
        "/exit",
    ]

    class MockPrompt:
        def __init__(self, *args, **kwargs):
            self.lines = list(inputs)

        async def get_input_async(self, prompt="you> "):
            if self.lines:
                return self.lines.pop(0)
            return "/exit"

    monkeypatch.setattr("agentcli.cli.InteractivePrompt", MockPrompt)

    args = argparse.Namespace(
        model=None,
        file=[],
        no_agents_md=True,
        show_model=False,
        resume=None,
        allow_write=False,
        plain=True,
        no_color=True,
        budget=None,
        max_cost=None,
    )
    config = Config()

    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-dummy"}):
        exit_code = await run_chat(args, config)
        assert exit_code == ExitCode.SUCCESS

    out, _ = capsys.readouterr()
    assert "Current budget tier" in out
    assert "Budget tier updated to: low" in out
    assert "Budget tier updated to: high" in out
    assert "Session budget ceiling set to: $0.5000 USD" in out
    assert "Session token budget ceiling set to: 50,000 tokens" in out
    assert "Budget and cost counters reset" in out
    assert "Token Usage" in out
    assert "Token & Cost Budget" in out


@pytest.mark.asyncio
async def test_tui_phase35_slash_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test TUI handles /budget, /cost, /tokens commands seamlessly."""
    from unittest.mock import AsyncMock, MagicMock

    from agentcli.ui.tui_app import TUIApplication

    session = MagicMock()
    session.governor = TokenBudgetGovernor(max_cost_usd=1.0)
    session.cumulative_cost_usd = 0.02
    session.get_session_stats = AsyncMock(
        return_value={"total_tokens": 1000, "user_tokens": 600, "assistant_tokens": 400}
    )
    session.config = Config()
    session.registry = None
    session.router = None

    tui = TUIApplication(config=session.config, session=session)
    mock_event = MagicMock()

    # 1. /cost
    await tui._handle_slash_command("/cost", "12:00:00", mock_event)
    assert any("Token & Cost Budget" in m[1] for m in tui.state.messages)

    # 2. /tokens
    await tui._handle_slash_command("/tokens", "12:00:01", mock_event)
    assert any("Token Usage:" in m[1] for m in tui.state.messages)

    # 3. /budget status
    await tui._handle_slash_command("/budget status", "12:00:02", mock_event)

    # 4. /budget set
    await tui._handle_slash_command("/budget set $2.50", "12:00:03", mock_event)
    assert session.governor.max_cost_usd == 2.50
    assert any("ceiling set to: $2.5000" in m[1] for m in tui.state.messages)

    # 5. /budget max-tokens
    await tui._handle_slash_command("/budget max-tokens 50000", "12:00:04", mock_event)
    assert session.governor.max_tokens == 50000

    # 6. /budget reset
    await tui._handle_slash_command("/budget reset", "12:00:05", mock_event)
    assert any("counters reset" in m[1] for m in tui.state.messages)

    # 7. /budget tier
    await tui._handle_slash_command("/budget tier high", "12:00:06", mock_event)
    assert session.config.routing.budget_tier == "high"

    # 8. /budget invalid
    await tui._handle_slash_command("/budget invalid_subcommand", "12:00:07", mock_event)


def test_adaptive_compressor_all_passes() -> None:
    """Explicitly verify passes 1, 2, 3, and 4 in AdaptiveContextCompressor.compress()."""
    compressor = AdaptiveContextCompressor(target_budget_ratio=1.0, keep_recent_turns=2)

    # 1. Pass 1 fit (t1 pruned tool output)
    compressor_small = AdaptiveContextCompressor(target_budget_ratio=1.0, max_tool_chars=50, keep_recent_turns=2)
    tool_history = [
        ChatMessage(role="user", content="run tool"),
        ChatMessage(role="assistant", content="```tool\n" + ("output\n" * 100) + "```"),
    ]
    res_t1 = compressor_small.compress(tool_history, max_context_tokens=80)
    assert len(res_t1) == 2
    assert "omitted by context governor" in (res_t1[1].content or "")

    # 2. Pass 2: compress_tier2 with keep_recent=2
    many_turns = [
        ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"turn {i}: " + ("context payload " * 20))
        for i in range(10)
    ]
    t2_direct = compressor.compress_tier2(many_turns, keep_recent=2)
    assert any("Milestone Context Summary" in (m.content or "") for m in t2_direct)
    assert len(t2_direct) == 5  # 1 summary + 4 recent

    # 3. Pass 3: compress_tier2 with keep_recent=1
    t2_tight_direct = compressor.compress_tier2(many_turns, keep_recent=1)
    assert any("Milestone Context Summary" in (m.content or "") for m in t2_tight_direct)
    assert len(t2_tight_direct) == 3  # 1 summary + 2 recent

    # 4. Pass 4: Tier 3 emergency reset
    t3_direct = compressor.compress_tier3_emergency(
        many_turns, user_goal="Emergency recovery", touched_files=["main.py"]
    )
    assert any("Emergency Context Budget Reset" in (m.content or "") for m in t3_direct)



