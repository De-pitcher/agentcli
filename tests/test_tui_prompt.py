"""Comprehensive tests for Phase 14 TUI Overhaul, prompt_toolkit integration, and status spinner."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from prompt_toolkit.document import Document

from agentcli.cli import build_parser
from agentcli.ui.prompt import (
    InteractivePrompt,
    SlashAndFileCompleter,
    get_history_file_path,
)
from agentcli.ui.render import ConsoleRenderer


def test_build_parser_plain_and_no_color_flags() -> None:
    """Test CLI parser recognizes --plain and --no-color flags."""
    parser = build_parser()
    args = parser.parse_args(["--plain", "--no-color", "chat"])
    assert args.plain is True
    assert args.no_color is True

    # Also test passing flags after the subparser command
    args_after = parser.parse_args(["chat", "--plain", "--no-color"])
    assert args_after.plain is True
    assert args_after.no_color is True

    args_run = parser.parse_args(["run", "my goal", "--plain"])
    assert args_run.plain is True
    assert args_run.goal == "my goal"



def test_get_history_file_path() -> None:
    """Test history file path is returned as a Path object."""
    path = get_history_file_path()
    assert isinstance(path, Path)
    assert path.name == "history"


def test_slash_and_file_completer_slash_commands() -> None:
    """Test SlashAndFileCompleter completes slash commands."""
    completer = SlashAndFileCompleter()
    doc = Document("/ex")
    completions = list(completer.get_completions(doc, None))  # type: ignore[arg-type]
    assert len(completions) == 1
    assert completions[0].text == "/exit"

    doc2 = Document("/q")
    completions2 = list(completer.get_completions(doc2, None))  # type: ignore[arg-type]
    assert len(completions2) == 1
    assert completions2[0].text == "/quit"


def test_slash_and_file_completer_file_references(tmp_path: Path, monkeypatch) -> None:
    """Test SlashAndFileCompleter completes @file references."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sample_test.py").write_text("print(1)")
    completer = SlashAndFileCompleter()
    doc = Document("analyze @sample")
    completions = list(completer.get_completions(doc, None))  # type: ignore[arg-type]
    assert len(completions) > 0
    assert any("sample_test.py" in str(c.display) or c.text == "_test.py" for c in completions)


def test_slash_and_file_completer_normal_text() -> None:
    """Test normal text without / or @ produces no completions."""
    completer = SlashAndFileCompleter()
    doc = Document("hello world")
    completions = list(completer.get_completions(doc, None))  # type: ignore[arg-type]
    assert len(completions) == 0


def test_interactive_prompt_fallback_single_line(monkeypatch, tmp_path: Path) -> None:
    """Test get_input in fallback/non-interactive mode with single line."""
    prompt = InteractivePrompt(history_file=tmp_path / "hist", plain=True)
    monkeypatch.setattr("builtins.input", lambda _: "test prompt")
    val = prompt.get_input("you> ")
    assert val == "test prompt"


def test_interactive_prompt_fallback_multiline(monkeypatch, tmp_path: Path) -> None:
    """Test get_input in fallback/non-interactive mode with trailing backslash continuation."""
    prompt = InteractivePrompt(history_file=tmp_path / "hist", plain=True)
    lines = ["first line\\", "second line"]
    monkeypatch.setattr("builtins.input", lambda _: lines.pop(0))
    val = prompt.get_input("you> ")
    assert val == "first line\nsecond line"


def test_console_renderer_status_spinner_plain() -> None:
    """Test status_spinner executes cleanly in plain mode."""
    renderer = ConsoleRenderer(plain=True)
    executed = False
    with renderer.status_spinner("Loading..."):
        executed = True
    assert executed is True


def test_console_renderer_status_spinner_rich(monkeypatch) -> None:
    """Test status_spinner executes cleanly when rich is active."""
    renderer = ConsoleRenderer(plain=False, no_color=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)

    executed = False
    with renderer.status_spinner("Testing spinner..."):
        executed = True
    assert executed is True


@pytest.mark.asyncio
async def test_interactive_prompt_async_fallback(monkeypatch, tmp_path: Path) -> None:
    """Test get_input_async in fallback/non-interactive mode."""
    prompt = InteractivePrompt(history_file=tmp_path / "hist", plain=True)
    monkeypatch.setattr("builtins.input", lambda _: "async prompt input")
    val = await prompt.get_input_async("you> ")
    assert val == "async prompt input"


