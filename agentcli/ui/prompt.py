"""Interactive terminal prompt powered by prompt_toolkit.

Provides multiline editing, persistent history, auto-completion for slash
commands and @file references, and seamless fallback for automated/non-TTY environments.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, ClassVar

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import CompleteEvent, Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style


class SlashAndFileCompleter(Completer):
    """Completer for slash commands (/exit, /quit, /help, /budget, /model, /goal, /tokens, /cost, /clear, /reset) and @file references."""

    SLASH_COMMANDS: ClassVar[list[tuple[str, str]]] = [
        ("/help", "Show help, slash commands, and shortcuts"),
        ("/budget", "View or set budget tier (low, medium, high)"),
        ("/model", "View or switch active model (or 'auto')"),
        ("/goal", "Run an autonomous multi-step goal loop"),
        ("/tokens", "Show current session token usage breakdown"),
        ("/cost", "Show current session estimated cost"),
        ("/clear", "Clear terminal screen"),
        ("/reset", "Reset session history and start fresh"),
        ("/exit", "Exit agentcli"),
        ("/quit", "Exit agentcli"),
    ]

    def __init__(self) -> None:
        self.path_completer = PathCompleter(expanduser=True)

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Any:
        text = document.text_before_cursor

        # Complete slash commands at the start of input
        if text.startswith("/"):
            for cmd, desc in self.SLASH_COMMANDS:
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text), display_meta=desc)
            return

        # Complete @file references anywhere in input
        last_at = text.rfind("@")
        if last_at != -1 and (last_at == 0 or text[last_at - 1].isspace()):
            sub_doc = Document(text[last_at + 1 :])
            for comp in self.path_completer.get_completions(sub_doc, complete_event):
                yield Completion(
                    comp.text,
                    start_position=comp.start_position,
                    display=comp.display,
                    display_meta="file",
                )


def get_history_file_path() -> Path:
    """Return the platform-appropriate path for persistent CLI history."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    history_dir = base / "agentcli"
    try:
        history_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        history_dir = Path.home() / ".agentcli"
        history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir / "history"


class InteractivePrompt:
    """Wrapper around prompt_toolkit PromptSession with fallback to standard input."""

    def __init__(
        self,
        history_file: Path | None = None,
        plain: bool = False,
    ) -> None:
        self.plain = plain
        self.history_path = history_file or get_history_file_path()
        self._session: PromptSession[str] | None = None

        if self.is_interactive:
            style = Style.from_dict(
                {
                    "prompt": "ansicyan bold",
                    "continuation": "ansibrightblack",
                }
            )
            self._session = PromptSession(
                history=FileHistory(str(self.history_path)),
                auto_suggest=AutoSuggestFromHistory(),
                completer=SlashAndFileCompleter(),
                style=style,
            )

    @property
    def is_interactive(self) -> bool:
        """Return True if prompt_toolkit can run interactively on stdout/stdin."""
        return sys.stdin.isatty() and sys.stdout.isatty() and not self.plain

    def _fallback_input(self, prompt_text: str = "you> ") -> str:
        """Non-TTY / scripted fallback with multi-line trailing backslash support."""
        lines: list[str] = []
        while True:
            prompt = f"\n{prompt_text}" if not lines else "... "
            line = input(prompt)
            if line.endswith("\\"):
                lines.append(line[:-1])
                continue
            lines.append(line)
            break
        return "\n".join(lines)

    async def get_input_async(self, prompt_text: str = "you> ") -> str:
        """Prompt the user for input asynchronously using prompt_toolkit or fallback."""
        if self._session is not None and self.is_interactive:
            return await self._session.prompt_async(
                [("class:prompt", f"\n{prompt_text}")],
            )
        return await asyncio.to_thread(self._fallback_input, prompt_text)

    def get_input(self, prompt_text: str = "you> ") -> str:
        """Prompt the user for input using prompt_toolkit (sync) or fallback."""
        if self._session is not None and self.is_interactive:
            return self._session.prompt(
                [("class:prompt", f"\n{prompt_text}")],
            )
        return self._fallback_input(prompt_text)

