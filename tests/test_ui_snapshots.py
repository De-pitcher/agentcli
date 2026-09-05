"""Terminal UI frame snapshots and visual layout test suite (Phase 28)."""

import pytest

from agentcli.config import Config
from agentcli.session import AgentSession
from agentcli.ui.render import ConsoleRenderer
from agentcli.ui.snapshot import VirtualTerminalBuffer, strip_ansi
from agentcli.ui.theme import (
    draw_box,
    render_badge,
    render_progress_bar,
)
from agentcli.ui.tui_app import TUIApplication


@pytest.fixture(autouse=True)
def set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-api-key")


def test_strip_ansi_sequences() -> None:
    """Test ANSI escape sequence removal."""
    colored_text = "\033[38;5;45m\033[1m[ gemma-4-31b ]\033[0m"
    assert strip_ansi(colored_text) == "[ gemma-4-31b ]"


def test_render_badge_styles() -> None:
    """Test styled badge rendering and plain text fallback."""
    accent_badge = render_badge("gemma-4-31b", style="accent")
    assert "gemma-4-31b" in accent_badge

    success_badge = render_badge("ONLINE", style="success")
    assert "ONLINE" in success_badge

    plain_badge = render_badge("OFFLINE", no_color=True)
    assert plain_badge == "[OFFLINE]"


def test_render_progress_bar_calculation() -> None:
    """Test progress bar visual fill and percentage formatting."""
    bar_50 = render_progress_bar(current=0.5, total=1.0, width=10, no_color=True)
    assert "50.0%" in bar_50
    assert len(bar_50.split()[0]) == 12  # '[' + 10 chars + ']'

    bar_100 = render_progress_bar(current=1.5, total=1.0, width=10, no_color=True)
    assert "100.0%" in bar_100

    bar_0 = render_progress_bar(current=0.0, total=0.0, width=10, no_color=True)
    assert "0.0%" in bar_0


def test_draw_box_formatting_and_width() -> None:
    """Test draw_box creates structured framed containers at exact width."""
    content = "Line 1: System initialized\nLine 2: Token budget normal"
    box = draw_box(title="Status Box", content=content, width=60, no_color=True)

    lines = box.splitlines()
    assert len(lines) >= 4
    assert "Status Box" in lines[0]
    # Every line must match target width
    for line in lines:
        assert len(line) == 60


def test_virtual_terminal_buffer_overflow_detection() -> None:
    """Test VirtualTerminalBuffer asserting maximum column limits."""
    buf = VirtualTerminalBuffer(cols=40)
    buf.write("Normal line inside column limit\n")
    buf.assert_no_overflow(max_cols=40)

    buf.write("This line is intentionally constructed to exceed the strict forty column limit\n")
    with pytest.raises(AssertionError, match="exceeds column width 40"):
        buf.assert_no_overflow(max_cols=40)


def test_console_renderer_step_tree(capsys: pytest.CaptureFixture[str]) -> None:
    """Test ConsoleRenderer.render_step_tree formatting."""
    renderer = ConsoleRenderer(plain=True)
    steps = [
        {"agent_type": "file_ops", "goal_criterion": "Read src/main.py"},
        {"agent_type": "code_analyzer", "goal_criterion": "Locate symbol Table"},
        {"agent_type": "shell", "goal_criterion": "Execute test suite"},
    ]

    renderer.render_step_tree(steps, current_index=1)
    captured = capsys.readouterr().out

    assert "Plan Execution Hierarchy:" in captured
    assert "[file_ops] Read src/main.py [DONE]" in captured
    assert "[code_analyzer] Locate symbol Table [RUNNING]" in captured
    assert "[shell] Execute test suite [PENDING]" in captured


def test_console_renderer_diff_preview(capsys: pytest.CaptureFixture[str]) -> None:
    """Test ConsoleRenderer.render_diff_preview summary calculation and box layout."""
    renderer = ConsoleRenderer(plain=True)
    diff = (
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-def old():\n"
        "+def new():\n"
        "+    return True\n"
    )

    renderer.render_diff_preview(diff, file_path="src/app.py")
    captured = capsys.readouterr().out

    assert "Diff Preview: src/app.py" in captured
    assert "+2 / -1 lines" in captured
    assert "def new():" in captured


def test_console_renderer_telemetry_banner(capsys: pytest.CaptureFixture[str]) -> None:
    """Test ConsoleRenderer.render_telemetry_banner output."""
    renderer = ConsoleRenderer(plain=True)
    renderer.render_telemetry_banner(
        prompt_tokens=1000,
        completion_tokens=250,
        latency_seconds=1.45,
        cost_usd=0.0018,
    )
    captured = capsys.readouterr().out

    assert "Latency: 1.45s" in captured
    assert "Tokens: 1,250" in captured
    assert "Spend: $0.0018" in captured


def test_tui_app_modal_inspectors() -> None:
    """Test TUIApplication modal inspector state toggling."""
    config = Config()
    session = AgentSession(config=config)
    tui = TUIApplication(config=config, session=session)

    # Initial state
    assert not tui.state.is_diff_modal_open
    assert not tui.state.is_history_modal_open

    # Set diff and inspect
    tui.state.diff_content = "diff --git a/test.py b/test.py"
    tui.state.is_diff_modal_open = True
    modal_rendered = tui._render_modal()
    assert len(modal_rendered) > 0
    assert "STEP DIFF INSPECTOR" in modal_rendered[0][1]

    # Close diff, open history
    tui.state.is_diff_modal_open = False
    tui.state.is_history_modal_open = True
    tui.state.history_items = ["[12:00] User: hi", "[12:01] Assistant: hello"]
    history_modal = tui._render_modal()
    assert len(history_modal) > 0
    assert "SESSION TIMELINE BROWSER" in history_modal[0][1]
