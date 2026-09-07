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
from prompt_toolkit.filters import has_completions
from prompt_toolkit.formatted_text import StyleAndTextTuples, to_formatted_text
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.styles import Style


def resolve_slash_command(text: str) -> str:
    """Normalize and auto-complete partial or backslash-prefixed slash commands.

    Handles leading backslashes (e.g. `\\model` -> `/model`), common typos/aliases
    (e.g. `/exist` -> `/exit`), and unambiguous partial prefixes (e.g. `/mod` -> `/model`).
    """
    s = text.strip()
    if not s or not s.startswith(("/", "\\")):
        return text

    if s.startswith("\\"):
        s = "/" + s[1:]

    parts = s.split(maxsplit=1)
    raw_cmd = parts[0].lower()
    args = f" {parts[1]}" if len(parts) > 1 else ""

    aliases: dict[str, str] = {
        "/exist": "/exit",
        "/quit": "/exit",
        "/q": "/exit",
        "/messages": "/history",
        "/hist": "/history",
        "/diffs": "/diff",
        "/cls": "/clear",
        "/h": "/help",
        "/rollback": "/undo",
        "/revert": "/undo",
        "/task": "/tasks",
        "/bg": "/tasks",
        "/skills": "/skill",
        "/worktree": "/branch",
        "/wt": "/branch",
        "/branches": "/branch",
        "/heal": "/healing",
        "/selfheal": "/healing",
    }

    if raw_cmd in aliases:
        return aliases[raw_cmd] + args

    canonical_commands = [
        "/help",
        "/history",
        "/budget",
        "/model",
        "/models",
        "/goal",
        "/diff",
        "/undo",
        "/tasks",
        "/skill",
        "/branch",
        "/healing",
        "/tokens",
        "/cost",
        "/clear",
        "/reset",
        "/exit",
    ]

    if raw_cmd in canonical_commands:
        return raw_cmd + args

    matches = [c for c in canonical_commands if c.startswith(raw_cmd)]
    if matches:
        return matches[0] + args

    return s


class SlashAndFileCompleter(Completer):
    """Completer for slash commands (/models, /model, /undo, /tasks, /skill, /branch, /healing, /budget, /history, /exit, etc.), model arguments, and @file references."""

    SLASH_COMMANDS: ClassVar[list[tuple[str, str]]] = [
        ("/help", "Show help, slash commands, and shortcuts"),
        ("/models", "List available models with [FREE] / [PAID] indicators"),
        ("/model", "Switch active model (e.g. /model <id> or /model auto)"),
        ("/history", "View conversation history in current session"),
        ("/budget", "View or set budget tier (low, medium, high)"),
        ("/goal", "Run an autonomous multi-step goal loop"),
        ("/diff", "Inspect file diffs generated during session"),
        ("/undo", "Revert latest file changes or inspect turn rollback (/undo diff)"),
        ("/tasks", "List or manage background tasks (/tasks, /tasks kill <id>)"),
        ("/skill", "Run or inspect custom skills and recipes (/skill list, /skill run <name>)"),
        ("/branch", "Manage Git worktrees and sandboxes (/branch list, /branch create <name>)"),
        ("/healing", "Manage self-healing, drift detection, and auto-rollback (/healing status, /healing rollback)"),
        ("/tokens", "Show current session token usage breakdown"),
        ("/cost", "Show current session estimated cost"),
        ("/clear", "Clear terminal screen"),
        ("/reset", "Reset session history and start fresh"),
        ("/exit", "Exit agentcli"),
        ("/quit", "Exit agentcli"),
    ]

    ALIASES: ClassVar[dict[str, str]] = {
        "/exist": "/exit",
        "/messages": "/history",
        "/diffs": "/diff",
        "/cls": "/clear",
        "/h": "/help",
        "/task": "/tasks",
        "/bg": "/tasks",
        "/skills": "/skill",
        "/worktree": "/branch",
        "/wt": "/branch",
        "/branches": "/branch",
        "/heal": "/healing",
        "/selfheal": "/healing",
    }



    def __init__(self) -> None:
        self.path_completer = PathCompleter(expanduser=True)

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Any:
        text = document.text_before_cursor

        # Complete model arguments after /model
        if text.startswith(("/model ", "\\model ")):
            from ..routing.registry import _BUILTIN_MODELS

            arg = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
            arg_lower = arg.lower()

            special_options = [
                ("auto", "[AUTO] Task-based model routing"),
                ("free", "[FILTER] List all free models"),
                ("paid", "[FILTER] List all paid models"),
            ]
            for opt, desc in special_options:
                if opt.startswith(arg_lower):
                    yield Completion(opt, start_position=-len(arg), display_meta=desc)

            for m in _BUILTIN_MODELS:
                if m.id.lower().startswith(arg_lower):
                    tag = "[FREE]" if m.is_free else "[PAID]"
                    ctx = (
                        f"{m.context_window // 1000}k"
                        if m.context_window >= 1000
                        else str(m.context_window)
                    )
                    yield Completion(
                        m.id,
                        start_position=-len(arg),
                        display_meta=f"{tag} ({ctx} ctx)",
                    )
            return

        # Complete budget arguments after /budget
        if text.startswith(("/budget ", "\\budget ")):
            arg = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
            arg_lower = arg.lower()
            budget_options = [
                ("status", "[ACTION] Show budget usage and velocity summary"),
                ("set", "[ACTION] Set USD budget ceiling (/budget set <amount>)"),
                ("max-tokens", "[ACTION] Set token ceiling (/budget max-tokens <count>)"),
                ("reset", "[ACTION] Reset session cost and token counters"),
                ("low", "[TIER] Free models only"),
                ("medium", "[TIER] High-efficiency & free models"),
                ("high", "[TIER] Frontier reasoning & coding models"),
            ]
            for opt, desc in budget_options:
                if opt.startswith(arg_lower):
                    yield Completion(opt, start_position=-len(arg), display_meta=desc)
            return

        # Complete skill arguments after /skill
        if text.startswith(("/skill ", "\\skill ")):
            from ..skills.loader import SkillLoader

            arg = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
            arg_lower = arg.lower()

            skill_actions = [
                ("list", "[ACTION] List all available skills"),
                ("info", "[ACTION] Show skill details and parameters"),
                ("run", "[ACTION] Execute a skill recipe (/skill run <name>)"),
                ("reload", "[ACTION] Reload skills from disk"),
            ]
            for act, desc in skill_actions:
                if act.startswith(arg_lower):
                    yield Completion(act, start_position=-len(arg), display_meta=desc)

            if arg_lower.startswith(("run ", "info ")):
                sub_parts = arg.split(maxsplit=1)
                sub_arg = sub_parts[1] if len(sub_parts) > 1 else ""
                loader = SkillLoader()
                for skill in loader.list_skills():
                    if skill.name.lower().startswith(sub_arg.lower()):
                        yield Completion(
                            skill.name,
                            start_position=-len(sub_arg),
                            display_meta=f"[{skill.source_type.upper()}] {skill.description[:35]}",
                        )
            return

        # Complete branch/worktree arguments after /branch or /worktree
        if text.startswith(("/branch ", "\\branch ", "/worktree ", "\\worktree ", "/wt ", "\\wt ")):
            from ..worktree.manager import WorktreeManager

            arg = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
            arg_lower = arg.lower()

            branch_actions = [
                ("list", "[ACTION] List all active Git worktrees"),
                ("create", "[ACTION] Create a new sandboxed worktree (/branch create <name>)"),
                ("status", "[ACTION] Show modified and dirty files in worktree"),
                ("diff", "[ACTION] Show unified diff vs base branch (/branch diff [name])"),
                ("merge", "[ACTION] Merge worktree back to base branch (/branch merge <name>)"),
                ("discard", "[ACTION] Prune worktree and delete sandbox (/branch discard <name>)"),
                ("prune", "[ACTION] Clean up stale or orphan worktree references"),
            ]
            for act, desc in branch_actions:
                if act.startswith(arg_lower):
                    yield Completion(act, start_position=-len(arg), display_meta=desc)

            if arg_lower.startswith(("status ", "diff ", "merge ", "discard ")):
                sub_parts = arg.split(maxsplit=1)
                sub_arg = sub_parts[1] if len(sub_parts) > 1 else ""
                wt_mgr = WorktreeManager()
                for wt in wt_mgr.list_worktrees():
                    if wt.branch.lower().startswith(sub_arg.lower()):
                        yield Completion(
                            wt.branch,
                            start_position=-len(sub_arg),
                            display_meta=f"[WORKTREE] {wt.path}",
                        )
            return

        # Complete healing arguments after /healing or /heal
        if text.startswith(("/healing ", "\\healing ", "/heal ", "\\heal ")):
            arg = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
            arg_lower = arg.lower()

            healing_actions = [
                ("status", "[ACTION] Show self-healing, drift detector, and snapshot status"),
                ("rollback", "[ACTION] Revert last or specific snapshot (/healing rollback [id])"),
                ("reset", "[ACTION] Reset drift history, failure counters, and snapshots"),
                ("enable", "[ACTION] Enable auto-healing snapshots and rollbacks"),
                ("disable", "[ACTION] Disable auto-healing snapshots and rollbacks"),
            ]
            for act, desc in healing_actions:
                if act.startswith(arg_lower):
                    yield Completion(act, start_position=-len(arg), display_meta=desc)
            return



        # Complete slash commands at the start of input (support both / and \)
        if text.startswith(("/", "\\")):
            normalized_text = "/" + text[1:] if text.startswith("\\") else text

            if normalized_text in self.ALIASES:
                target = self.ALIASES[normalized_text]
                for cmd, desc in self.SLASH_COMMANDS:
                    if cmd == target:
                        yield Completion(cmd, start_position=-len(text), display_meta=desc)
                        return

            for cmd, desc in self.SLASH_COMMANDS:
                if cmd.startswith(normalized_text):
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
        input: Any = None,
        output: Any = None,
    ) -> None:
        self.plain = plain
        self.input = input
        self.output = output
        self.history_path = history_file or get_history_file_path()
        self._session: PromptSession[str] | None = None

        if self.is_interactive:
            style = Style.from_dict(
                {
                    "prompt": "#00d7ff bold",
                    "prompt_symbol": "#00ffaf bold",
                    "continuation": "#585858 italic",
                    "completion-menu": "bg:#262626 #ffffff",
                    "completion-menu.completion": "bg:#262626 #ffffff",
                    "completion-menu.completion.current": "bg:#005f87 #ffffff bold",
                    "completion-menu.meta": "bg:#303030 #87d7ff",
                    "completion-menu.meta.completion.current": "bg:#005f87 #87ffff bold",
                    "scrollbar.background": "bg:#262626",
                    "scrollbar.button": "bg:#585858",
                }
            )
            kb = KeyBindings()

            @kb.add("enter", filter=has_completions)
            def _accept_completion_on_enter(event: KeyPressEvent) -> None:
                buff = event.current_buffer
                if buff.complete_state:
                    if buff.complete_state.current_completion:
                        buff.apply_completion(buff.complete_state.current_completion)
                    elif buff.complete_state.completions:
                        buff.apply_completion(buff.complete_state.completions[0])
                buff.validate_and_handle()

            self._session = PromptSession(
                history=FileHistory(str(self.history_path)),
                auto_suggest=AutoSuggestFromHistory(),
                completer=SlashAndFileCompleter(),
                complete_while_typing=True,
                key_bindings=kb,
                style=style,
                input=self.input,
                output=self.output,
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
        raw_result = "\n".join(lines)
        return resolve_slash_command(raw_result)

    async def get_input_async(self, prompt_text: str = "you> ") -> str:
        """Prompt the user for input asynchronously using prompt_toolkit or fallback."""
        if self._session is not None and self.is_interactive:
            formatted_prompt: StyleAndTextTuples
            if prompt_text in ("you> ", "you ❯ "):
                formatted_prompt = [("class:prompt", "\nyou "), ("class:prompt_symbol", "❯ ")]
            else:
                formatted_prompt = [("class:prompt", f"\n{prompt_text}")]
            raw_result = await self._session.prompt_async(to_formatted_text(formatted_prompt))
            return resolve_slash_command(raw_result)
        return await asyncio.to_thread(self._fallback_input, prompt_text)

    def get_input(self, prompt_text: str = "you> ") -> str:
        """Prompt the user for input using prompt_toolkit (sync) or fallback."""
        if self._session is not None and self.is_interactive:
            formatted_prompt: StyleAndTextTuples
            if prompt_text in ("you> ", "you ❯ "):
                formatted_prompt = [("class:prompt", "\nyou "), ("class:prompt_symbol", "❯ ")]
            else:
                formatted_prompt = [("class:prompt", f"\n{prompt_text}")]
            raw_result = self._session.prompt(to_formatted_text(formatted_prompt))
            return resolve_slash_command(raw_result)
        return self._fallback_input(prompt_text)
