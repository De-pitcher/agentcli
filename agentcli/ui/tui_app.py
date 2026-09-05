"""Full-screen interactive Terminal User Interface (TUI) dashboard (Phase 21).

Provides multi-pane visual observability, live conversation stream, sub-agent
execution tree, token/cost telemetry gauge, and interactive modal inspection.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout.containers import Float, FloatContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.styles import Style

from .prompt import SlashAndFileCompleter

if TYPE_CHECKING:
    import argparse

    from ..config import Config
    from ..session import AgentSession

logger = logging.getLogger(__name__)


@dataclass
class TUIState:
    """State model for the interactive TUI application."""

    messages: list[tuple[str, str, str]] = field(default_factory=list)  # (role, text, time)
    subagent_logs: list[str] = field(default_factory=list)
    subagent_status: dict[str, str] = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    budget_limit_usd: float = 0.0
    active_model: str = "auto"
    active_preset: str = "coding"
    focused_pane: str = "input"
    status_line: str = "Ready. [Tab] Switch Focus | [Ctrl+O] Diffs | [Ctrl+H] History | [Ctrl+C] Exit"
    is_diff_modal_open: bool = False
    diff_content: str = ""
    is_history_modal_open: bool = False
    history_items: list[str] = field(default_factory=list)

    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class TUIApplication:
    """Full-screen TUI Dashboard powered by prompt_toolkit."""

    def __init__(self, config: Config, session: AgentSession | None = None) -> None:
        self.config = config
        self.session = session
        self.state = TUIState(
            active_model=config.openrouter.default_model or "auto",
            active_preset=getattr(config.app, "preset", "coding") or "coding",
            budget_limit_usd=getattr(config.routing, "max_cost_usd", 0.0) or 0.0,
        )

        self.input_buffer = Buffer(
            completer=SlashAndFileCompleter(),
            multiline=False,
            name="input_buffer",
        )

        self.kb = KeyBindings()
        self._setup_keybindings()
        self._app: Application[None] | None = None

    def _setup_keybindings(self) -> None:
        @self.kb.add("c-c")
        @self.kb.add("c-d")
        def _exit(event: KeyPressEvent) -> None:
            event.app.exit()

        @self.kb.add("tab")
        def _cycle_focus(event: KeyPressEvent) -> None:
            panes = ["input", "chat", "agents", "metrics"]
            try:
                idx = panes.index(self.state.focused_pane)
                self.state.focused_pane = panes[(idx + 1) % len(panes)]
            except ValueError:
                self.state.focused_pane = "input"
            self.state.status_line = f"Focused Pane: {self.state.focused_pane.upper()} | [Tab] Next Pane | [Ctrl+C] Exit"

        @self.kb.add("c-o")
        def _toggle_diff(event: KeyPressEvent) -> None:
            self.state.is_diff_modal_open = not self.state.is_diff_modal_open
            if self.state.is_diff_modal_open and not self.state.diff_content:
                self.state.diff_content = "No modified file diffs available in current session."

        @self.kb.add("c-h")
        def _toggle_history(event: KeyPressEvent) -> None:
            self.state.is_history_modal_open = not self.state.is_history_modal_open
            if self.state.is_history_modal_open and not self.state.history_items:
                self.state.history_items = [
                    f"[{m[2]}] {m[0].upper()}: {m[1][:60]}..." for m in self.state.messages
                ] or ["No session history recorded yet."]

        @self.kb.add("escape")
        def _close_modals(event: KeyPressEvent) -> None:
            self.state.is_diff_modal_open = False
            self.state.is_history_modal_open = False

        @self.kb.add("enter", filter=Condition(lambda: self.state.focused_pane == "input"))
        def _submit_input(event: KeyPressEvent) -> None:
            text = self.input_buffer.text.strip()
            if not text:
                return
            self.input_buffer.reset()
            t_now = datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S")
            self.add_message("user", text, t_now)
            if text in ("/exit", "/quit"):
                event.app.exit()
                return

            if self.session:
                asyncio.create_task(self._process_user_query(text))

    async def _process_user_query(self, text: str) -> None:
        """Asynchronously process user input with the underlying AgentSession."""
        if not self.session:
            return

        t_now = datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S")
        self.state.status_line = f"Processing query via {self.state.active_model}..."
        try:
            self.add_subagent_event("loop", f"Dispatched turn: {text[:40]}...")
            reply = await self.session.step(text)
            self.add_message("assistant", reply or "(empty response)", t_now)
            self.state.status_line = "Ready. [Tab] Focus | [Ctrl+O] Diffs | [Ctrl+H] History | [Ctrl+C] Exit"
        except Exception as exc:  # noqa: BLE001
            self.add_message("error", f"Error: {exc}", t_now)
            self.state.status_line = f"Execution error: {exc}"

    def add_message(self, role: str, text: str, timestamp: str | None = None) -> None:
        """Add a conversation message to the main stream pane."""
        t = timestamp or datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S")
        self.state.messages.append((role, text, t))

    def add_subagent_event(self, agent_type: str, event_text: str) -> None:
        """Record an event in the sub-agent execution tree pane."""
        t = datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S")
        log_entry = f"[{t}] [{agent_type}] {event_text}"
        self.state.subagent_logs.append(log_entry)
        self.state.subagent_status[agent_type] = event_text

    def update_telemetry(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Update token usage and financial cost metrics."""
        self.state.prompt_tokens += prompt_tokens
        self.state.completion_tokens += completion_tokens
        self.state.cached_tokens += cached_tokens
        self.state.cost_usd += cost_usd

    def _render_header(self) -> StyleAndTextTuples:
        model = self.state.active_model
        preset = self.state.active_preset
        cost = f"${self.state.cost_usd:.4f}"
        tokens = f"{self.state.total_tokens():,} tok"
        return [
            ("class:header", f"  agentcli v2.2.0 | Model: {model} | Preset: {preset} | Spend: {cost} ({tokens})  "),
        ]

    def _render_chat(self) -> StyleAndTextTuples:
        lines: StyleAndTextTuples = []
        if not self.state.messages:
            lines.append(("class:muted", "  (No messages yet. Type your query below and press Enter)\n"))
            return lines

        # Render recent messages
        for role, text, t in self.state.messages[-30:]:
            role_class = f"class:role_{role.lower()}"
            lines.append(("class:timestamp", f"[{t}] "))
            lines.append((role_class, f"{role.upper()}: "))
            lines.append(("class:text", f"{text}\n\n"))
        return lines

    def _render_agents(self) -> StyleAndTextTuples:
        lines: StyleAndTextTuples = [("class:title", "  ⚡ SUB-AGENTS & PEER SWARM\n")]
        if not self.state.subagent_status:
            lines.append(("class:muted", "  No active sub-agents running.\n"))
        else:
            for agent, status in self.state.subagent_status.items():
                lines.append(("class:accent", f"  • {agent}: "))
                lines.append(("class:text", f"{status[:40]}\n"))

        lines.append(("class:title", "\n  📋 RECENT EVENTS\n"))
        for log in self.state.subagent_logs[-10:]:
            lines.append(("class:muted", f"  {log}\n"))
        return lines

    def _render_telemetry(self) -> StyleAndTextTuples:
        lines: StyleAndTextTuples = [("class:title", "  📊 TELEMETRY & BUDGET\n")]
        p_tok = self.state.prompt_tokens
        c_tok = self.state.completion_tokens
        cache_tok = self.state.cached_tokens
        total_tok = self.state.total_tokens()
        cost = self.state.cost_usd
        limit = self.state.budget_limit_usd

        lines.append(("class:text", f"  Prompt Tokens:     {p_tok:,}\n"))
        lines.append(("class:text", f"  Completion Tokens: {c_tok:,}\n"))
        lines.append(("class:text", f"  Cached Tokens:     {cache_tok:,}\n"))
        lines.append(("class:text", f"  Total Tokens:      {total_tok:,}\n"))
        lines.append(("class:accent", f"  Session Cost:      ${cost:.4f}\n"))

        if limit > 0.0:
            pct = min(1.0, cost / limit)
            bar_len = 16
            filled = int(pct * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            lines.append(("class:text", f"  Budget Limit:      ${limit:.2f}\n"))
            lines.append(("class:bar", f"  [{bar}] {pct:.1%}\n"))
        return lines

    def _render_status(self) -> StyleAndTextTuples:
        return [("class:status", f" {self.state.status_line} ")]

    def _render_modal(self) -> StyleAndTextTuples:
        if self.state.is_diff_modal_open:
            return [
                ("class:modal_title", " 📝 STEP DIFF INSPECTOR [Esc to close] \n\n"),
                ("class:modal_content", self.state.diff_content),
            ]
        if self.state.is_history_modal_open:
            content = "\n".join(self.state.history_items)
            return [
                ("class:modal_title", " 📜 SESSION TIMELINE BROWSER [Esc to close] \n\n"),
                ("class:modal_content", content),
            ]
        return []

    def build_layout(self) -> Layout:
        """Construct the prompt_toolkit multi-pane layout."""
        header_win = Window(content=FormattedTextControl(self._render_header), height=1)
        chat_win = Window(content=FormattedTextControl(self._render_chat), wrap_lines=True)
        agents_win = Window(content=FormattedTextControl(self._render_agents), wrap_lines=True)
        telemetry_win = Window(content=FormattedTextControl(self._render_telemetry), height=8)

        right_pane = HSplit([agents_win, telemetry_win])
        body_pane = VSplit([chat_win, right_pane])

        input_win = Window(content=BufferControl(buffer=self.input_buffer), height=2)
        status_win = Window(content=FormattedTextControl(self._render_status), height=1)

        modal_win = Window(
            content=FormattedTextControl(self._render_modal),
            wrap_lines=True,
            style="class:modal",
        )

        root_container = FloatContainer(
            content=HSplit([header_win, body_pane, input_win, status_win]),
            floats=[
                Float(
                    content=modal_win,
                    top=2,
                    bottom=2,
                    left=4,
                    right=4,
                    hide_when_covering_content=False,
                )
            ]
            if (self.state.is_diff_modal_open or self.state.is_history_modal_open)
            else [],
        )

        return Layout(root_container)

    def create_application(
        self,
        input: Any = None,
        output: Any = None,
    ) -> Application[None]:
        """Create configured prompt_toolkit Application instance."""
        style = Style.from_dict(
            {
                "header": "bg:#005577 #ffffff bold",
                "title": "#00d7ff bold",
                "accent": "#00ffaf bold",
                "muted": "#767676 italic",
                "timestamp": "#585858",
                "role_user": "#5fd7ff bold",
                "role_assistant": "#87ff87 bold",
                "role_tool": "#ffd75f bold",
                "role_error": "#ff5f5f bold",
                "status": "bg:#303030 #d0d0d0",
                "modal": "bg:#1c1c1c #ffffff border:#00d7ff",
                "modal_title": "bg:#005577 #ffffff bold",
                "modal_content": "#d0d0d0",
                "bar": "#00ffaf",
            }
        )

        self._app = Application(
            layout=self.build_layout(),
            key_bindings=self.kb,
            style=style,
            full_screen=True,
            mouse_support=True,
            input=input,
            output=output,
        )
        return self._app


async def run_tui(args: argparse.Namespace, config: Config) -> int:
    """Entrypoint to launch the full-screen TUI dashboard."""
    from ..session import AgentSession

    if getattr(args, "budget", None):
        config.routing.budget_tier = args.budget
    if getattr(args, "max_cost", None) is not None:
        config.routing.max_cost_usd = args.max_cost

    session = AgentSession(config=config)
    await session.initialize_mcp()

    tui = TUIApplication(config=config, session=session)
    app = tui.create_application()

    try:
        await app.run_async()
    finally:
        await session.aclose()

    return 0
