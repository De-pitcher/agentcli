"""Full-screen interactive Terminal User Interface (TUI) dashboard (Phase 21).

Provides multi-pane visual observability, live conversation stream, sub-agent
execution tree, token/cost telemetry gauge, and interactive modal inspection.
"""

from __future__ import annotations

import asyncio
import datetime
import inspect
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition, has_completions
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent, merge_key_bindings
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.styles import Style

from .. import __version__
from .prompt import SlashAndFileCompleter, resolve_slash_command

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
    status_line: str = (
        "Ready. [Tab] Switch Focus | [Ctrl+O] Diffs | [Ctrl+Y]/[F2] History | [Ctrl+C] Exit"
    )
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

        # Pre-populate existing session message history into chat state
        if self.session and self.session.history:
            for m in self.session.history:
                if m.role in ("user", "assistant"):
                    self.state.messages.append((m.role, m.content or "", "history"))

        self.input_buffer = Buffer(
            completer=SlashAndFileCompleter(),
            complete_while_typing=True,
            multiline=False,
            name="input_buffer",
        )

        self._is_processing = False
        self._current_task: asyncio.Task[Any] | None = None
        self.kb = KeyBindings()
        self._setup_keybindings()
        self.merged_kb = merge_key_bindings([load_key_bindings(), self.kb])
        self._app: Application[None] | None = None

    def _setup_keybindings(self) -> None:
        @self.kb.add("c-c")
        def _handle_ctrl_c(event: KeyPressEvent) -> None:
            if (
                self._is_processing
                and self._current_task is not None
                and not self._current_task.done()
            ):
                self._current_task.cancel()
                self.state.status_line = (
                    "Cancelling current execution... Press [Ctrl+C] again to exit"
                )
                event.app.invalidate()
            else:
                event.app.exit()

        @self.kb.add("c-d")
        def _exit(event: KeyPressEvent) -> None:
            event.app.exit()

        @self.kb.add("tab", filter=~has_completions)
        def _cycle_focus(event: KeyPressEvent) -> None:
            panes = ["input", "chat", "agents", "metrics"]
            try:
                idx = panes.index(self.state.focused_pane)
                self.state.focused_pane = panes[(idx + 1) % len(panes)]
            except ValueError:
                self.state.focused_pane = "input"
            self.state.status_line = f"Focused Pane: {self.state.focused_pane.upper()} | [Tab] Switch Focus | [Ctrl+C] Exit"
            event.app.invalidate()

        @self.kb.add("tab", filter=has_completions)
        def _next_completion(event: KeyPressEvent) -> None:
            if self.input_buffer.complete_state:
                self.input_buffer.complete_next()
            event.app.invalidate()

        @self.kb.add("s-tab", filter=has_completions)
        def _prev_completion(event: KeyPressEvent) -> None:
            if self.input_buffer.complete_state:
                self.input_buffer.complete_previous()
            event.app.invalidate()

        @self.kb.add("down", filter=has_completions)
        def _down_completion(event: KeyPressEvent) -> None:
            if self.input_buffer.complete_state:
                self.input_buffer.complete_next()
            event.app.invalidate()

        @self.kb.add("up", filter=has_completions)
        def _up_completion(event: KeyPressEvent) -> None:
            if self.input_buffer.complete_state:
                self.input_buffer.complete_previous()
            event.app.invalidate()

        @self.kb.add("c-o")
        def _toggle_diff(event: KeyPressEvent) -> None:
            self.state.is_diff_modal_open = not self.state.is_diff_modal_open
            if self.state.is_diff_modal_open and not self.state.diff_content:
                self.state.diff_content = "Loading modified workspace diffs..."
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._load_diff_async())
                except RuntimeError:
                    pass
            event.app.invalidate()

        @self.kb.add("c-y")
        @self.kb.add("f2")
        def _toggle_history(event: KeyPressEvent) -> None:
            self.state.is_history_modal_open = not self.state.is_history_modal_open
            if self.state.is_history_modal_open:
                self.state.history_items = [
                    f"[{m[2] or 'history'}] {m[0].upper()}: {m[1][:60]}..."
                    for m in self.state.messages
                ] or ["No session history recorded yet."]
            event.app.invalidate()

        @self.kb.add("escape")
        def _close_modals(event: KeyPressEvent) -> None:
            self.state.is_diff_modal_open = False
            self.state.is_history_modal_open = False
            event.app.invalidate()

        @self.kb.add("enter")
        def _submit_input(event: KeyPressEvent) -> None:
            if self.state.is_diff_modal_open or self.state.is_history_modal_open:
                self.state.is_diff_modal_open = False
                self.state.is_history_modal_open = False
                event.app.invalidate()
                return

            if self.input_buffer.complete_state is not None:
                if self.input_buffer.complete_state.current_completion:
                    self.input_buffer.apply_completion(
                        self.input_buffer.complete_state.current_completion
                    )
                elif self.input_buffer.complete_state.completions:
                    self.input_buffer.apply_completion(
                        self.input_buffer.complete_state.completions[0]
                    )

            text = self.input_buffer.text.strip()
            if not text:
                return

            text = resolve_slash_command(text)

            if self._is_processing:
                self.state.status_line = (
                    "Busy processing... Press [Ctrl+C] to cancel current execution."
                )
                event.app.invalidate()
                return

            self.input_buffer.reset()
            t_now = datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S")

            if text in ("/exit", "/quit"):
                event.app.exit()
                return

            if text.startswith("/"):
                asyncio.create_task(self._handle_slash_command(text, t_now, event))
                return

            self.add_message("user", text, t_now)
            if self.session:
                self._current_task = asyncio.create_task(self._process_user_query(text))

    async def _handle_slash_command(self, text: str, timestamp: str, event: KeyPressEvent) -> None:
        """Process slash commands directly inside the TUI dashboard."""
        cmd = text.split()[0].lower()

        if cmd in {"/exit", "/quit", "/exist", "/q"}:
            if event and event.app:
                event.app.exit()
            return

        if cmd in {"/help", "/h"}:
            help_text = (
                "Available Slash Commands:\n"
                "  /help                 Show this help message\n"
                "  /models [free|paid]   List available models with [FREE] / [PAID] indicators\n"
                "  /model <model|auto>   View or switch active model\n"
                "  /history, /messages   Browse full conversation history modal\n"
                "  /budget [tier]        View or set budget tier (low, medium, high)\n"
                "  /goal <description>   Run an autonomous multi-step goal loop\n"
                "  /diff, /diffs         View workspace file diffs modal\n"
                "  /tokens, /cost        View session token usage and spending\n"
                "  /clear, /cls          Clear chat messages and sub-agent logs\n"
                "  /reset                Clear conversation and auto-ground workspace\n"
                "  /exit, /quit          Exit the TUI dashboard"
            )
            self.add_message("system", help_text, timestamp)
            return

        if cmd in {"/history", "/messages"}:
            self.state.is_history_modal_open = True
            self.state.history_items = [
                f"[{m[2] or 'history'}] {m[0].upper()}: {m[1][:60]}..." for m in self.state.messages
            ] or ["No session history recorded yet."]
            if self._app is not None:
                self._app.invalidate()
            return

        if cmd in {"/budget", "\\budget"}:
            parts = text.split(maxsplit=2)
            subcmd = parts[1].lower() if len(parts) > 1 else ""
            val = parts[2].strip() if len(parts) > 2 else ""

            if not subcmd or subcmd in ("status", "info", "show"):
                curr = self.config.routing.budget_tier
                msg = f"Current budget tier: {curr}"
                if self.session and hasattr(self.session, "governor"):
                    msg += "\n" + self.session.governor.format_summary()
                self.add_message("system", msg, timestamp)
            elif subcmd in ("low", "medium", "high", "tier"):
                tier_name = val if subcmd == "tier" else subcmd
                if tier_name in ("low", "medium", "high"):
                    self.config.routing.budget_tier = tier_name
                    if self.session and self.session.router is not None:
                        self.session.router._budget_tier = tier_name
                    self.add_message("system", f"Budget tier updated to: {tier_name}", timestamp)
                else:
                    self.add_message(
                        "system",
                        f"Invalid budget tier '{tier_name}'. Choose from: low, medium, high",
                        timestamp,
                    )
            elif subcmd in ("set", "limit", "max-cost"):
                if not val:
                    self.add_message("system", "Usage: /budget set <amount_usd>", timestamp)
                else:
                    try:
                        cost_limit = float(val.lstrip("$"))
                        if self.session and hasattr(self.session, "governor"):
                            self.session.governor.set_budget(max_cost_usd=cost_limit)
                        self.config.routing.max_cost_usd = cost_limit
                        self.state.budget_limit_usd = cost_limit
                        self.add_message(
                            "system", f"Session budget ceiling set to: ${cost_limit:.4f} USD", timestamp
                        )
                    except ValueError:
                        self.add_message(
                            "system", f"Invalid budget amount: '{val}'. Must be a positive number.", timestamp
                        )
            elif subcmd in ("tokens", "max-tokens"):
                if not val:
                    self.add_message("system", "Usage: /budget max-tokens <count>", timestamp)
                else:
                    try:
                        t_count = int(val.replace(",", ""))
                        if self.session and hasattr(self.session, "governor"):
                            self.session.governor.set_budget(max_tokens=t_count)
                        self.add_message(
                            "system", f"Session token budget ceiling set to: {t_count:,} tokens", timestamp
                        )
                    except ValueError:
                        self.add_message("system", f"Invalid token count: '{val}'.", timestamp)
            elif subcmd in ("reset", "clear"):
                if self.session and hasattr(self.session, "governor"):
                    self.session.governor.reset()
                    self.session.cumulative_cost_usd = 0.0
                self.add_message("system", "Budget and cost counters reset for current session.", timestamp)
            else:
                self.add_message(
                    "system", f"Invalid budget subcommand '{subcmd}'. Choose from: low, medium, high, status, set, max-tokens, reset", timestamp
                )
            return

        if cmd in {"/models", "/model"}:
            parts = text.split(maxsplit=1)
            if len(parts) == 1 or parts[1].strip().lower() in {"list", "free", "paid"}:
                filter_type = (
                    parts[1].strip().lower()
                    if len(parts) > 1 and parts[1].strip().lower() in {"free", "paid"}
                    else None
                )
                from ..routing.registry import _BUILTIN_MODELS, format_models_text

                active = (
                    self.session.forced_model
                    if self.session and self.session.forced_model
                    else self.state.active_model
                )
                models_list = (
                    self.session.registry.all_models()
                    if (self.session and self.session.registry)
                    else list(_BUILTIN_MODELS)
                )
                table_text = format_models_text(
                    models_list, active_model=active, filter_type=filter_type
                )
                self.add_message(
                    "system", f"🤖 Available Models Catalog:\n\n{table_text}", timestamp
                )
            else:
                target_model = parts[1].strip()
                if target_model.lower() == "auto":
                    if self.session:
                        self.session.forced_model = None
                        if self.session.registry is not None:
                            from ..routing.router import Router

                            self.session.router = Router(
                                self.session.registry,
                                self.config.routing.max_fallbacks,
                                budget_tier=self.config.routing.budget_tier,
                            )
                    self.state.active_model = "auto"
                    self.add_message(
                        "system",
                        "Switched to auto model routing [FREE/PAID auto-selection].",
                        timestamp,
                    )
                else:
                    if self.session:
                        self.session.forced_model = target_model
                        self.session.router = None
                    self.state.active_model = target_model
                    is_free = target_model.endswith(":free")
                    tag = "[FREE]" if is_free else "[PAID]"
                    self.add_message(
                        "system", f"Forced model set to: {target_model} {tag}", timestamp
                    )
            return

        if cmd in {"/tokens", "/cost"}:
            if self.session:
                stats = await self.session.get_session_stats()
                cost = self.session.cumulative_cost_usd
                self.update_telemetry(
                    prompt_tokens=stats.get("user_tokens", 0) - self.state.prompt_tokens,
                    completion_tokens=stats.get("assistant_tokens", 0)
                    - self.state.completion_tokens,
                    cost_usd=cost - self.state.cost_usd,
                )
                msg_text = (
                    f"Token Usage: {stats['total_tokens']} total "
                    f"({stats['user_tokens']} prompt, {stats['assistant_tokens']} completion) | "
                    f"Estimated Cost: ${cost:.6f} USD"
                )
                if hasattr(self.session, "governor"):
                    msg_text += "\n" + self.session.governor.format_summary()
                self.add_message("system", msg_text, timestamp)
            return

        if cmd in {"/clear", "/cls"}:
            self.state.messages.clear()
            self.state.subagent_logs.clear()
            self.state.subagent_status.clear()
            self.add_message("system", "Chat history and sub-agent logs cleared.", timestamp)
            return

        if cmd == "/reset":
            if self.session:
                self.session.history.clear()
                await self.session.auto_ground_workspace()
            self.state.messages.clear()
            self.add_message("system", "Session reset. Conversation history cleared.", timestamp)
            return

        if cmd in {"/diff", "/diffs"}:
            self.state.is_diff_modal_open = True
            await self._load_diff_async()
            return

        if cmd in {"/undo", "/rollback", "/revert"}:
            if self.session and hasattr(self.session, "checkpoint_manager"):
                parts = text.split(maxsplit=1)
                sub = parts[1].strip().lower() if len(parts) > 1 else ""
                if sub == "diff":
                    diff_res = self.session.checkpoint_manager.get_diff()
                    self.state.diff_content = diff_res
                    self.state.is_diff_modal_open = True
                elif sub in ("list", "history"):
                    cps = self.session.checkpoint_manager.list_checkpoints()
                    if not cps:
                        self.add_message(
                            "system", "No checkpoints recorded in this session.", timestamp
                        )
                    else:
                        lines = ["Available Checkpoints:"]
                        for cp in cps:
                            lines.append(
                                f"  [{cp['id']}] {cp['description']} ({cp['file_count']} files)"
                            )
                        self.add_message("system", "\n".join(lines), timestamp)
                else:
                    res = self.session.checkpoint_manager.rollback()
                    if res["success"]:
                        reverted = res.get("reverted_files", [])
                        msg = f"Rollback successful (Checkpoint {res.get('checkpoint_id')}). Reverted {len(reverted)} file(s)."
                        if reverted:
                            msg += "\n" + "\n".join(f"  - {f}" for f in reverted)
                        self.add_message("system", msg, timestamp)
                    else:
                        self.add_message(
                            "system", f"Rollback failed: {res.get('error')}", timestamp
                        )
            else:
                self.add_message(
                    "system", "No active session checkpoint manager available.", timestamp
                )
            return

        if cmd in {"/tasks", "/task", "/bg"}:
            if self.session and hasattr(self.session, "task_manager"):
                parts = text.split(maxsplit=2)
                sub = parts[1].lower() if len(parts) > 1 else "list"
                target_id = parts[2].strip() if len(parts) > 2 else ""

                if sub in ("list", "ls") or (len(parts) == 1):
                    tasks = self.session.task_manager.list_tasks()
                    if not tasks:
                        self.add_message(
                            "system", "No background tasks currently active.", timestamp
                        )
                    else:
                        lines = [f"Background Tasks ({len(tasks)} active):"]
                        for t in tasks:
                            lines.append(
                                f"  [{t['id']}] {t['command']} ({t['status']}, {t['uptime_seconds']}s, {t['total_lines']} lines)"
                            )
                        self.add_message("system", "\n".join(lines), timestamp)
                elif sub == "status":
                    if not target_id:
                        self.add_message("system", "Usage: /tasks status <task_id>", timestamp)
                    else:
                        status_info = self.session.task_manager.get_status(target_id)
                        if "error" in status_info:
                            self.add_message("system", f"Error: {status_info['error']}", timestamp)
                        else:
                            lines = [f"Task [{target_id}] Status:"]
                            for k, v in status_info.items():
                                lines.append(f"  {k}: {v}")
                            self.add_message("system", "\n".join(lines), timestamp)
                elif sub in ("logs", "log"):
                    if not target_id:
                        self.add_message("system", "Usage: /tasks logs <task_id>", timestamp)
                    else:
                        log_info = self.session.task_manager.get_logs(target_id, tail=20)
                        if "error" in log_info:
                            self.add_message("system", f"Error: {log_info['error']}", timestamp)
                        else:
                            lines = [f"--- Logs for [{target_id}] ---"]
                            lines.extend(log_info.get("lines", []))
                            self.add_message("system", "\n".join(lines), timestamp)
                elif sub in ("kill", "stop"):
                    if not target_id:
                        self.add_message("system", "Usage: /tasks kill <task_id>", timestamp)
                    else:
                        res = await self.session.task_manager.kill_task(target_id)
                        if res.get("success"):
                            self.add_message("system", f"Task [{target_id}] terminated.", timestamp)
                        else:
                            self.add_message(
                                "system",
                                f"Failed to terminate [{target_id}]: {res.get('error')}",
                                timestamp,
                            )
                else:
                    self.add_message(
                        "system",
                        "Usage: /tasks [list | status <id> | logs <id> | kill <id>]",
                        timestamp,
                    )
            else:
                self.add_message("system", "No active session task manager available.", timestamp)
            return

        if cmd in {"/skill", "/skills"}:
            if self.session and hasattr(self.session, "skill_engine"):
                parts = text.split(maxsplit=2)
                sub = parts[1].lower() if len(parts) > 1 else "list"
                target_name = parts[2].strip() if len(parts) > 2 else ""

                if sub in ("list", "ls") or (len(parts) == 1):
                    skills = self.session.skill_engine.list_available_skills()
                    if not skills:
                        self.add_message(
                            "system", "No skills discovered in workspace or user directory.", timestamp
                        )
                    else:
                        lines = [f"Available Skills ({len(skills)}):"]
                        for s in skills:
                            lines.append(
                                f"  [{s['source_type'].upper()}] {s['name']} (v{s['version']}): {s['description']}"
                            )
                        self.add_message("system", "\n".join(lines), timestamp)
                elif sub == "info":
                    if not target_name:
                        self.add_message("system", "Usage: /skill info <skill_name>", timestamp)
                    else:
                        manifest = self.session.skill_engine.loader.get_skill(target_name)
                        if not manifest:
                            self.add_message(
                                "system", f"Skill '{target_name}' not found.", timestamp
                            )
                        else:
                            lines = [
                                f"Skill [{manifest.name}] (v{manifest.version})",
                                f"  Description: {manifest.description}",
                                f"  Source: {manifest.source_type}",
                                f"  Execution Mode: {manifest.execution_mode}",
                            ]
                            if manifest.parameters:
                                lines.append("  Parameters:")
                                for p_name, p in manifest.parameters.items():
                                    lines.append(f"    - {p_name} [{p.type}]: {p.description}")
                            self.add_message("system", "\n".join(lines), timestamp)
                elif sub == "reload":
                    self.session.skill_engine.loader.reload()
                    total = len(self.session.skill_engine.loader.list_skills())
                    self.add_message(
                        "system", f"Reloaded skills. Found {total} skill(s).", timestamp
                    )
                elif sub in ("run", "exec") or self.session.skill_engine.loader.has_skill(sub):
                    actual_name = (
                        target_name.split(maxsplit=1)[0] if sub in ("run", "exec") else sub
                    )
                    raw_args = (
                        target_name.split(maxsplit=1)[1]
                        if (sub in ("run", "exec") and len(target_name.split(maxsplit=1)) > 1)
                        else target_name
                    )
                    if not actual_name:
                        self.add_message(
                            "system", "Usage: /skill run <skill_name> [param=value ...]", timestamp
                        )
                    else:
                        parsed_args = {}
                        if raw_args:
                            for token in raw_args.split():
                                if "=" in token:
                                    k, v = token.split("=", 1)
                                    parsed_args[k.strip()] = v.strip()
                                else:
                                    parsed_args["target"] = token
                        try:
                            manifest, rendered = self.session.skill_engine.prepare_skill(
                                skill_name=actual_name,
                                arguments=parsed_args,
                            )
                            self.add_message("user", f"/skill run {actual_name}", timestamp)
                            self._current_task = asyncio.create_task(
                                self._process_goal_query(rendered)
                            )
                        except Exception as exc:  # noqa: BLE001
                            self.add_message("system", f"Error: {exc}", timestamp)
                else:
                    self.add_message(
                        "system",
                        "Usage: /skill [list | info <name> | run <name> [args] | reload]",
                        timestamp,
                    )
            else:
                self.add_message("system", "No active session skill engine available.", timestamp)
            return

        if cmd in ("/branch", "/worktree", "/wt"):
            parts = text.split(maxsplit=2)
            subcmd = parts[1].lower() if len(parts) > 1 else "list"
            target = parts[2].strip() if len(parts) > 2 else ""

            if self.session and hasattr(self.session, "worktree_manager"):
                wt_mgr = self.session.worktree_manager
                if subcmd in ("list", "ls") or len(parts) == 1:
                    wts = wt_mgr.list_worktrees()
                    if not wts:
                        self.add_message("system", "No active Git worktrees. Create with /branch create <name>", timestamp)
                    else:
                        lines = [f"Active Worktrees ({len(wts)}):"]
                        for wt in wts:
                            lines.append(f"  • [{wt.status.upper()}] {wt.branch} -> {wt.path} (base: {wt.base_ref})")
                        self.add_message("system", "\n".join(lines), timestamp)
                elif subcmd in ("create", "add", "new"):
                    if not target:
                        self.add_message("system", "Usage: /branch create <branch_name> [base_ref]", timestamp)
                    else:
                        c_parts = target.split(maxsplit=1)
                        b_name = c_parts[0].strip()
                        b_base = c_parts[1].strip() if len(c_parts) > 1 else None
                        try:
                            meta = wt_mgr.create_worktree(b_name, base_ref=b_base)
                            self.add_message(
                                "system",
                                f"Created worktree for '{meta.branch}' at {meta.path} (base: {meta.base_ref})",
                                timestamp,
                            )
                        except Exception as exc:  # noqa: BLE001
                            self.add_message("system", f"Error creating worktree: {exc}", timestamp)
                elif subcmd == "diff":
                    target_b = target or (wt_mgr.list_worktrees()[0].branch if wt_mgr.list_worktrees() else "")
                    if not target_b:
                        self.add_message("system", "Usage: /branch diff <branch_name>", timestamp)
                    else:
                        try:
                            diff_res = wt_mgr.compute_diff(target_b)
                            msg = diff_res if diff_res.strip() else f"No diff for sandbox '{target_b}'."
                            self.add_message("system", f"--- Diff for {target_b} ---\n{msg}", timestamp)
                        except Exception as exc:  # noqa: BLE001
                            self.add_message("system", f"Error computing diff: {exc}", timestamp)
                elif subcmd in ("discard", "delete", "remove"):
                    if not target:
                        self.add_message("system", "Usage: /branch discard <branch_name>", timestamp)
                    else:
                        removed = wt_mgr.remove_worktree(target, force=True, delete_branch=True)
                        if removed:
                            self.add_message("system", f"Pruned and discarded worktree '{target}'.", timestamp)
                        else:
                            self.add_message("system", f"Failed to remove worktree '{target}'.", timestamp)
                else:
                    self.add_message(
                        "system",
                        "Usage: /branch [list | create <name> | diff <name> | merge <name> | discard <name> | prune]",
                        timestamp,
                    )
            else:
                self.add_message("system", "Worktree manager not available in active session.", timestamp)
            return

        if cmd in ("/budget", "/cost", "/tokens"):
            if self.session and hasattr(self.session, "governor"):
                parts = text.split(maxsplit=2)
                subcmd = parts[1].lower() if len(parts) > 1 else "status"
                val = parts[2].strip() if len(parts) > 2 else ""

                if cmd in ("/cost", "/tokens") or subcmd in ("status", "info", "show") or len(parts) == 1:
                    self.add_message("system", self.session.governor.format_summary(), timestamp)
                elif subcmd in ("set", "limit", "max-cost"):
                    if not val:
                        self.add_message("system", "Usage: /budget set <amount_usd>", timestamp)
                    else:
                        try:
                            f_val = float(val.lstrip("$"))
                            self.session.governor.set_budget(max_cost_usd=f_val)
                            self.add_message("system", f"Session budget ceiling set to: ${f_val:.4f} USD", timestamp)
                        except ValueError:
                            self.add_message("system", f"Invalid budget amount: '{val}'.", timestamp)
                elif subcmd in ("tokens", "max-tokens"):
                    if not val:
                        self.add_message("system", "Usage: /budget max-tokens <count>", timestamp)
                    else:
                        try:
                            t_val = int(val.replace(",", ""))
                            self.session.governor.set_budget(max_tokens=t_val)
                            self.add_message("system", f"Session token budget ceiling set to: {t_val:,} tokens", timestamp)
                        except ValueError:
                            self.add_message("system", f"Invalid token count: '{val}'.", timestamp)
                elif subcmd in ("reset", "clear"):
                    self.session.governor.reset()
                    self.session.cumulative_cost_usd = 0.0
                    self.add_message("system", "Budget and cost counters reset for current session.", timestamp)
                elif subcmd in ("low", "medium", "high", "tier"):
                    tier_name = val if subcmd == "tier" else subcmd
                    if tier_name in ("low", "medium", "high"):
                        self.session.config.routing.budget_tier = tier_name
                        self.add_message("system", f"Budget routing tier switched to: [{tier_name.upper()}]", timestamp)
                    else:
                        self.add_message("system", "Usage: /budget tier [low | medium | high]", timestamp)
                else:
                    self.add_message("system", "Usage: /budget [status | set <amount> | max-tokens <count> | tier <low|med|high> | reset]", timestamp)
            else:
                self.add_message("system", "Budget governor not available in active session.", timestamp)
            return

        if cmd == "/goal":

            parts = text.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                self.add_message("system", "Usage: /goal <task description>", timestamp)
                return
            goal_text = parts[1].strip()
            self.add_message("user", f"/goal {goal_text}", timestamp)
            self._current_task = asyncio.create_task(self._process_goal_query(goal_text))
            return

        self.add_message(
            "system",
            f"Unknown slash command '{cmd}'. Type /help for available commands.",
            timestamp,
        )

    async def _load_diff_async(self) -> None:
        """Asynchronously load git diff from workspace sub-agent."""
        try:
            from ..subagents.base import SubAgentTask, SubAgentType
            from ..subagents.workspace import WorkspaceAgent

            agent = WorkspaceAgent()
            task = SubAgentTask(
                agent_type=SubAgentType.WORKSPACE, payload={"operation": "git_diff"}
            )
            res = await agent.run(task)
            diff = res.output.get("diff", "") if res.success else ""
            self.state.diff_content = (
                diff or "No modified file diffs available in current workspace."
            )
        except Exception as exc:  # noqa: BLE001
            self.state.diff_content = f"Failed to load diff: {exc}"
        if self._app is not None:
            self._app.invalidate()

    async def _sync_telemetry(self) -> None:
        """Synchronize telemetry state from active session."""
        if not self.session:
            return
        if hasattr(self.session, "get_session_stats") and callable(self.session.get_session_stats):
            try:
                res = self.session.get_session_stats()
                stats = await res if inspect.isawaitable(res) else res
                if isinstance(stats, dict):
                    self.state.prompt_tokens = stats.get("user_tokens", 0)
                    self.state.completion_tokens = stats.get("assistant_tokens", 0)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to fetch session stats: %s", exc)
        self.state.cost_usd = getattr(self.session, "cumulative_cost_usd", 0.0)
        if self._app is not None:
            self._app.invalidate()

    async def _process_user_query(self, text: str) -> None:
        """Asynchronously process user input with the underlying AgentSession."""
        if not self.session:
            return

        self._is_processing = True
        t_now = datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S")
        stop_spinner = asyncio.Event()
        spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

        async def _spinner_loop() -> None:
            idx = 0
            while not stop_spinner.is_set():
                frame = spinner_frames[idx % len(spinner_frames)]
                self.state.status_line = (
                    f"{frame} Thinking via {self.state.active_model}... [Ctrl+C] Abort"
                )
                if self._app is not None:
                    self._app.invalidate()
                try:
                    await asyncio.wait_for(stop_spinner.wait(), timeout=0.15)
                except TimeoutError:
                    pass
                idx += 1

        spinner_task = asyncio.create_task(_spinner_loop())
        try:
            self.add_subagent_event("session", f"Processing: {text[:40]}...")
            reply = await self.session.step(text)
            self.add_message("assistant", reply or "(empty response)", t_now)
            await self._sync_telemetry()
            self.state.status_line = (
                "Ready. [Tab] Switch Focus | [Ctrl+O] Diffs | [Ctrl+Y]/[F2] History | [Ctrl+C] Exit"
            )
        except asyncio.CancelledError:
            self.add_message("system", "Operation cancelled by user.", t_now)
            self.state.status_line = "Processing cancelled."
        except Exception as exc:  # noqa: BLE001
            self.add_message("error", f"Error: {exc}", t_now)
            self.state.status_line = f"Execution error: {exc}"
        finally:
            stop_spinner.set()
            await spinner_task
            self._is_processing = False
            self._current_task = None
            if self._app is not None:
                self._app.invalidate()

    async def _process_goal_query(self, goal_text: str) -> None:
        """Execute autonomous multi-step goal loop and display telemetry in TUI."""
        if not self.session:
            return

        self._is_processing = True
        t_now = datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S")
        stop_spinner = asyncio.Event()
        spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

        async def _spinner_loop() -> None:
            idx = 0
            while not stop_spinner.is_set():
                frame = spinner_frames[idx % len(spinner_frames)]
                self.state.status_line = f"{frame} Autonomous Goal Loop running... [Ctrl+C] Abort"
                if self._app is not None:
                    self._app.invalidate()
                try:
                    await asyncio.wait_for(stop_spinner.wait(), timeout=0.15)
                except TimeoutError:
                    pass
                idx += 1

        spinner_task = asyncio.create_task(_spinner_loop())
        try:
            self.add_subagent_event("planner", f"Planning goal: {goal_text[:40]}...")
            async for event in self.session.run_loop(goal_text):
                from ..agent.events import (
                    FinishEvent,
                    PlanEvent,
                    ReflectEvent,
                    StepResultEvent,
                    StepStartEvent,
                )

                if isinstance(event, PlanEvent):
                    self.add_subagent_event("planner", f"Plan generated: {len(event.plan)} step(s)")
                elif isinstance(event, StepStartEvent):
                    payload_summary = str(
                        event.payload.get("operation")
                        or event.payload.get("query")
                        or event.payload.get("file")
                        or ""
                    )
                    self.add_subagent_event(
                        str(event.agent_type),
                        f"Executing step {event.step_index}: {payload_summary[:35]}",
                    )
                elif isinstance(event, StepResultEvent):
                    is_ok = bool(event.result and event.result.success)
                    agent_name = (
                        event.result.agent_type.value
                        if event.result and hasattr(event.result.agent_type, "value")
                        else "step"
                    )
                    st = "Done" if is_ok else "Failed"
                    self.add_subagent_event(agent_name, f"{st} ({event.duration_seconds:.1f}s)")
                elif isinstance(event, ReflectEvent):
                    self.add_subagent_event(
                        "reflector", f"Decision: {event.decision} - {event.reason[:30]}"
                    )
                elif isinstance(event, FinishEvent):
                    self.add_message(
                        "assistant", f"🎯 **Goal Accomplished**\n\n{event.summary}", t_now
                    )
            await self._sync_telemetry()
            self.state.status_line = (
                "Ready. [Tab] Switch Focus | [Ctrl+O] Diffs | [Ctrl+Y]/[F2] History | [Ctrl+C] Exit"
            )
        except asyncio.CancelledError:
            self.add_message("system", "Goal execution cancelled by user.", t_now)
            self.state.status_line = "Execution cancelled."
        except Exception as exc:  # noqa: BLE001
            self.add_message("error", f"Goal execution error: {exc}", t_now)
            self.state.status_line = f"Execution error: {exc}"
        finally:
            stop_spinner.set()
            await spinner_task
            self._is_processing = False
            self._current_task = None
            if self._app is not None:
                self._app.invalidate()

    def add_message(self, role: str, text: str, timestamp: str | None = None) -> None:
        """Add a conversation message to the main stream pane."""
        t = timestamp or datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S")
        self.state.messages.append((role, text, t))
        if self._app is not None:
            self._app.invalidate()

    def add_subagent_event(self, agent_type: str, event_text: str) -> None:
        """Record an event in the sub-agent execution tree pane."""
        t = datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S")
        log_entry = f"[{t}] [{agent_type}] {event_text}"
        self.state.subagent_logs.append(log_entry)
        self.state.subagent_status[agent_type] = event_text
        if self._app is not None:
            self._app.invalidate()

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
        if self._app is not None:
            self._app.invalidate()

    def _render_header(self) -> StyleAndTextTuples:
        model = self.state.active_model
        is_free = model.endswith(":free") or model == "auto"
        tag = "[FREE]" if is_free else "[PAID]"
        preset = self.state.active_preset
        cost = f"${self.state.cost_usd:.4f}"
        tokens = f"{self.state.total_tokens():,} tok"
        return [
            (
                "class:header",
                f"  agentcli v{__version__} | Model: {model} {tag} | Preset: {preset} | Spend: {cost} ({tokens})  ",
            ),
        ]

    def _render_chat(self) -> StyleAndTextTuples:
        lines: StyleAndTextTuples = []
        if not self.state.messages:
            lines.append(
                ("class:muted", "  (No messages yet. Type your query below and press Enter)\n")
            )
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
        modal_container = ConditionalContainer(
            modal_win,
            filter=Condition(
                lambda: bool(self.state.is_diff_modal_open or self.state.is_history_modal_open)
            ),
        )

        root_container = FloatContainer(
            content=HSplit([header_win, body_pane, input_win, status_win]),
            floats=[
                Float(
                    content=modal_container,
                    top=2,
                    bottom=2,
                    left=4,
                    right=4,
                    hide_when_covering_content=False,
                ),
                Float(
                    attach_to_window=input_win,
                    content=CompletionsMenu(max_height=8),
                    ycursor=True,
                    xcursor=True,
                ),
            ],
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
                "completion-menu": "bg:#262626 #ffffff",
                "completion-menu.completion": "bg:#262626 #ffffff",
                "completion-menu.completion.current": "bg:#005f87 #ffffff bold",
                "completion-menu.meta": "bg:#303030 #87d7ff",
                "completion-menu.meta.completion.current": "bg:#005f87 #87ffff bold",
                "scrollbar.background": "bg:#262626",
                "scrollbar.button": "bg:#585858",
            }
        )

        self._app = Application(
            layout=self.build_layout(),
            key_bindings=self.merged_kb,
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

    resume_id = getattr(args, "resume", None)
    forced_model = getattr(args, "model", None)

    session = AgentSession(
        config=config,
        forced_model=forced_model,
        session_id=resume_id,
    )
    await session.initialize_mcp()

    tui = TUIApplication(config=config, session=session)
    app = tui.create_application()

    try:
        await app.run_async()
    finally:
        await session.aclose()

    return 0
