"""Comprehensive unit and integration tests for Phase 21: Full-Screen Interactive TUI Dashboard."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcli.cli import build_parser
from agentcli.config import Config
from agentcli.ui.tui_app import TUIApplication, TUIState


def test_tui_state_initialization_and_metrics() -> None:
    state = TUIState(
        active_model="anthropic/claude-3.5-sonnet",
        active_preset="coding",
        budget_limit_usd=5.0,
    )
    assert state.active_model == "anthropic/claude-3.5-sonnet"
    assert state.active_preset == "coding"
    assert state.total_tokens() == 0

    state.prompt_tokens = 1000
    state.completion_tokens = 250
    state.cached_tokens = 500
    state.cost_usd = 0.0045

    assert state.total_tokens() == 1250


def test_tui_app_rendering_helpers() -> None:
    config = Config()
    config.openrouter.default_model = "test-model"
    config.routing.max_cost_usd = 2.0

    tui = TUIApplication(config=config)

    # 1. Header
    header_tuples = tui._render_header()
    header_text = "".join(t[1] for t in header_tuples)
    assert "test-model" in header_text
    assert "agentcli" in header_text

    # 2. Chat empty & populated
    chat_empty = tui._render_chat()
    assert "No messages yet" in "".join(t[1] for t in chat_empty)

    tui.add_message("user", "Hello world")
    tui.add_message("assistant", "Hi there!")
    chat_populated = tui._render_chat()
    chat_text = "".join(t[1] for t in chat_populated)
    assert "USER: Hello world" in chat_text
    assert "ASSISTANT: Hi there!" in chat_text

    # 3. Sub-agents & logs
    agents_empty = tui._render_agents()
    assert "No active sub-agents" in "".join(t[1] for t in agents_empty)

    tui.add_subagent_event("code_analyzer", "analyzing src/main.py")
    agents_populated = tui._render_agents()
    agents_text = "".join(t[1] for t in agents_populated)
    assert "code_analyzer" in agents_text
    assert "analyzing src/main.py" in agents_text

    # 4. Telemetry & progress bar
    tui.update_telemetry(prompt_tokens=500, completion_tokens=100, cached_tokens=50, cost_usd=0.50)
    telemetry_tuples = tui._render_telemetry()
    telemetry_text = "".join(t[1] for t in telemetry_tuples)
    assert "Prompt Tokens:     500" in telemetry_text
    assert "Completion Tokens: 100" in telemetry_text
    assert "Session Cost:      $0.5000" in telemetry_text
    assert "Budget Limit:      $2.00" in telemetry_text
    assert "25.0%" in telemetry_text

    # 5. Status line
    status_tuples = tui._render_status()
    assert "Ready" in "".join(t[1] for t in status_tuples)

    # 6. Modals
    assert len(tui._render_modal()) == 0

    tui.state.is_diff_modal_open = True
    tui.state.diff_content = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@"
    diff_tuples = tui._render_modal()
    assert "STEP DIFF INSPECTOR" in "".join(t[1] for t in diff_tuples)
    assert "--- a/file.py" in "".join(t[1] for t in diff_tuples)

    tui.state.is_diff_modal_open = False
    tui.state.is_history_modal_open = True
    tui.state.history_items = ["[12:00:00] USER: Hello", "[12:00:01] ASSISTANT: Hi"]
    hist_tuples = tui._render_modal()
    assert "SESSION TIMELINE BROWSER" in "".join(t[1] for t in hist_tuples)
    assert "USER: Hello" in "".join(t[1] for t in hist_tuples)


from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput


def test_tui_app_layout_and_creation() -> None:
    config = Config()
    tui = TUIApplication(config=config)
    layout = tui.build_layout()
    assert layout is not None

    app = tui.create_application(input=DummyInput(), output=DummyOutput())
    assert app is not None
    assert app.full_screen is True


def test_tui_keybindings_and_focus_cycling() -> None:
    config = Config()
    tui = TUIApplication(config=config)

    mock_app = MagicMock()
    mock_event = MagicMock()
    mock_event.app = mock_app

    handlers = {b.handler.__name__: b.handler for b in tui.kb.bindings}

    # Test tab focus cycling
    tab_handler = handlers["_cycle_focus"]
    assert tui.state.focused_pane == "input"

    tab_handler(mock_event)
    assert tui.state.focused_pane == "chat"

    tab_handler(mock_event)
    assert tui.state.focused_pane == "agents"

    tab_handler(mock_event)
    assert tui.state.focused_pane == "metrics"

    tab_handler(mock_event)
    assert tui.state.focused_pane == "input"

    # Test Ctrl+O modal toggle
    diff_handler = handlers["_toggle_diff"]
    diff_handler(mock_event)
    assert tui.state.is_diff_modal_open is True

    diff_handler(mock_event)
    assert tui.state.is_diff_modal_open is False

    # Test Ctrl+Y / F2 history toggle
    hist_handler = handlers["_toggle_history"]
    hist_handler(mock_event)
    assert tui.state.is_history_modal_open is True

    # Test Escape closes modals
    esc_handler = handlers["_close_modals"]
    esc_handler(mock_event)
    assert tui.state.is_history_modal_open is False

    # Test merged keybindings has default editing bindings loaded
    assert len(tui.merged_kb.bindings) > len(tui.kb.bindings)


@pytest.mark.asyncio
async def test_tui_process_user_query_success() -> None:
    config = Config()
    mock_session = MagicMock()
    mock_session.step = AsyncMock(return_value="Calculated 42")

    tui = TUIApplication(config=config, session=mock_session)
    mock_app = MagicMock()
    tui._app = mock_app

    await tui._process_user_query("What is the meaning of life?")

    mock_session.step.assert_awaited_once_with("What is the meaning of life?")
    messages = tui.state.messages
    assert len(messages) == 1
    assert messages[0][0] == "assistant"
    assert messages[0][1] == "Calculated 42"
    assert "Ready" in tui.state.status_line
    assert mock_app.invalidate.call_count >= 1


@pytest.mark.asyncio
async def test_tui_process_user_query_error_handling() -> None:
    config = Config()
    mock_session = MagicMock()
    mock_session.step = AsyncMock(side_effect=RuntimeError("API Gateway Timeout"))

    tui = TUIApplication(config=config, session=mock_session)
    await tui._process_user_query("Trigger failure")

    messages = tui.state.messages
    assert len(messages) == 1
    assert messages[0][0] == "error"
    assert "API Gateway Timeout" in messages[0][1]
    assert "Execution error" in tui.state.status_line


def test_cli_tui_subparser() -> None:
    parser = build_parser()
    args = parser.parse_args(["tui", "--budget", "high", "--max-cost", "3.5", "--allow-write"])
    assert args.command == "tui"
    assert args.budget == "high"
    assert args.max_cost == 3.5
    assert args.allow_write is True


def test_tui_backspace_not_intercepted_by_history() -> None:
    config = Config()
    tui = TUIApplication(config=config)
    # Ensure 'c-h' (ASCII 0x08 / Backspace) is NOT bound to any custom handler
    bound_keys = [b.keys for b in tui.kb.bindings]
    for key_tuple in bound_keys:
        assert ("c-h",) not in bound_keys, (
            "c-h must not be bound as it intercepts Backspace in terminal mode"
        )


@pytest.mark.asyncio
async def test_tui_spinner_animation_frames() -> None:
    import asyncio

    config = Config()
    mock_session = MagicMock()

    async def slow_step(text: str) -> str:
        await asyncio.sleep(0.18)
        return "Slow result"

    mock_session.step = AsyncMock(side_effect=slow_step)
    tui = TUIApplication(config=config, session=mock_session)
    mock_app = MagicMock()
    tui._app = mock_app

    await tui._process_user_query("Calculate slow operation")
    assert mock_app.invalidate.call_count >= 2
    assert "Ready" in tui.state.status_line


@pytest.mark.asyncio
async def test_tui_slash_commands_handling() -> None:
    config = Config()
    mock_session = MagicMock()
    mock_session.get_session_stats = AsyncMock(
        return_value={"total_tokens": 300, "user_tokens": 200, "assistant_tokens": 100}
    )
    mock_session.cumulative_cost_usd = 0.005
    mock_session.history = []
    mock_session.auto_ground_workspace = AsyncMock(return_value=None)
    mock_session.forced_model = None
    mock_session.router = MagicMock()

    tui = TUIApplication(config=config, session=mock_session)
    mock_event = MagicMock()

    # 1. /help
    await tui._handle_slash_command("/help", "12:00:00", mock_event)
    assert any("Available Slash Commands:" in m[1] for m in tui.state.messages)

    # 2. /budget
    await tui._handle_slash_command("/budget medium", "12:00:01", mock_event)
    assert config.routing.budget_tier == "medium"

    # 3. /model
    await tui._handle_slash_command("/model openai/gpt-4o", "12:00:02", mock_event)
    assert tui.state.active_model == "openai/gpt-4o"
    assert mock_session.forced_model == "openai/gpt-4o"

    # 4. /tokens & /cost
    await tui._handle_slash_command("/tokens", "12:00:03", mock_event)
    assert tui.state.prompt_tokens == 200
    assert tui.state.completion_tokens == 100

    # 5. /clear
    await tui._handle_slash_command("/clear", "12:00:04", mock_event)
    assert len(tui.state.messages) == 1
    assert "cleared" in tui.state.messages[0][1]

    # 6. /reset
    await tui._handle_slash_command("/reset", "12:00:05", mock_event)
    mock_session.auto_ground_workspace.assert_awaited_once()

    # 7. /history
    await tui._handle_slash_command("/history", "12:00:06", mock_event)
    assert tui.state.is_history_modal_open is True


@pytest.mark.asyncio
async def test_tui_cancellation_on_ctrl_c() -> None:
    import asyncio

    config = Config()
    mock_session = MagicMock()

    async def hanging_step(text: str) -> str:
        await asyncio.sleep(10.0)
        return "Done"

    mock_session.step = AsyncMock(side_effect=hanging_step)
    tui = TUIApplication(config=config, session=mock_session)
    mock_app = MagicMock()
    tui._app = mock_app

    query_task = asyncio.create_task(tui._process_user_query("Long hanging query"))
    tui._current_task = query_task

    await asyncio.sleep(0.05)
    assert tui._is_processing is True

    # Simulate Ctrl+C keypress handler
    handlers = {b.handler.__name__: b.handler for b in tui.kb.bindings}
    ctrl_c_handler = handlers["_handle_ctrl_c"]
    mock_event = MagicMock()
    ctrl_c_handler(mock_event)

    # App exit should NOT be called; instead task should be cancelled
    mock_event.app.exit.assert_not_called()
    await asyncio.sleep(0.05)
    assert query_task.done() or query_task.cancelled()
    assert tui._is_processing is False
    assert any("cancelled" in m[1].lower() for m in tui.state.messages)
