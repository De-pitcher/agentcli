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


def test_resolve_slash_command_variations() -> None:
    """Test resolve_slash_command handles backslashes, aliases, typos, and prefixes."""
    from agentcli.ui.prompt import resolve_slash_command

    # Backslashes
    assert resolve_slash_command(r"\model") == "/model"
    assert resolve_slash_command(r"\model gpt-4") == "/model gpt-4"
    assert resolve_slash_command(r"\exit") == "/exit"
    assert resolve_slash_command(r"\quit") == "/exit"

    # Typos & aliases
    assert resolve_slash_command("/exist") == "/exit"
    assert resolve_slash_command(r"\exist") == "/exit"
    assert resolve_slash_command("/messages") == "/history"
    assert resolve_slash_command("/hist") == "/history"
    assert resolve_slash_command("/diffs") == "/diff"
    assert resolve_slash_command("/cls") == "/clear"
    assert resolve_slash_command("/h") == "/help"
    assert resolve_slash_command("/q") == "/exit"
    assert resolve_slash_command("/rollback") == "/undo"
    assert resolve_slash_command("/revert") == "/undo"

    # Prefix expansion
    assert resolve_slash_command("/mod") == "/model"
    assert (
        resolve_slash_command("/mod anthropic/claude-3.5-sonnet")
        == "/model anthropic/claude-3.5-sonnet"
    )
    assert resolve_slash_command("/ex") == "/exit"
    assert resolve_slash_command("/und") == "/undo"
    assert resolve_slash_command("/bud high") == "/budget high"
    assert resolve_slash_command("/tok") == "/tokens"
    assert resolve_slash_command("/cos") == "/cost"
    assert resolve_slash_command("/cle") == "/clear"
    assert resolve_slash_command("/res") == "/reset"

    # Non-slash text unchanged
    assert resolve_slash_command("hello world") == "hello world"
    assert resolve_slash_command("@file.py") == "@file.py"
    assert resolve_slash_command("") == ""


def test_slash_and_file_completer_backslash_and_aliases() -> None:
    """Test SlashAndFileCompleter works with backslashes and aliases."""
    completer = SlashAndFileCompleter()

    # Backslash prefix
    doc_mod = Document(r"\mod")
    comp_mod = list(completer.get_completions(doc_mod, None))  # type: ignore[arg-type]
    assert len(comp_mod) == 2
    assert {c.text for c in comp_mod} == {"/model", "/models"}

    # Alias / typo
    doc_exist = Document(r"\exist")
    comp_exist = list(completer.get_completions(doc_exist, None))  # type: ignore[arg-type]
    assert len(comp_exist) == 1
    assert comp_exist[0].text == "/exit"

    # Full list on slash and backslash
    doc_slash = Document("/")
    comp_slash = list(completer.get_completions(doc_slash, None))  # type: ignore[arg-type]
    assert len(comp_slash) == len(SlashAndFileCompleter.SLASH_COMMANDS)

    doc_bslash = Document("\\")
    comp_bslash = list(completer.get_completions(doc_bslash, None))  # type: ignore[arg-type]
    assert len(comp_bslash) == len(SlashAndFileCompleter.SLASH_COMMANDS)


def test_interactive_prompt_session_configuration(monkeypatch, tmp_path: Path) -> None:
    """Test InteractivePrompt configures completion and styles in interactive mode."""
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.output import DummyOutput

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    prompt = InteractivePrompt(
        history_file=tmp_path / "hist",
        plain=False,
        input=DummyInput(),
        output=DummyOutput(),
    )
    assert prompt._session is not None
    assert prompt._session.completer is not None
    assert prompt._session.complete_while_typing is not None
