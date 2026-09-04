"""Terminal UI and formatted rendering for agentcli.

Provides markdown rendering, styled loop events, sessions tables,
file previews, and diagnostic badges with automatic non-TTY / NO_COLOR fallback.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any


class ConsoleRenderer:
    """Centralized terminal renderer with rich formatting and plain-text fallback."""

    def __init__(self, plain: bool = False, no_color: bool = False) -> None:
        self.plain = plain
        self.no_color = no_color
        self._console: Any = None
        self._rich_available: bool | None = None

    @property
    def is_rich_enabled(self) -> bool:
        """Return True if rich color rendering should be used."""
        if self.plain or self.no_color:
            return False
        if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
            return False
        if not sys.stdout.isatty():
            return False
        if self._rich_available is None:
            try:
                import rich  # noqa: F401

                self._rich_available = True
            except ImportError:  # pragma: no cover
                self._rich_available = False
        return self._rich_available

    @property
    def console(self) -> Any:
        if self._console is None:
            from rich.console import Console

            self._console = Console(
                file=sys.stdout,
                no_color=self.no_color,
                force_terminal=True if self.is_rich_enabled else None,
                highlight=False,
            )
        return self._console

    def print_chunk(self, chunk: str) -> None:
        """Stream an individual assistant text token to stdout without delay."""
        sys.stdout.write(chunk)
        sys.stdout.flush()

    @contextmanager
    def status_spinner(self, message: str) -> Iterator[None]:
        """Display an animated status spinner during long-running tasks if interactive."""
        if self.is_rich_enabled:
            with self.console.status(f"[cyan]{message}[/cyan]", spinner="dots"):
                yield
        else:
            yield

    def render_markdown(self, text: str) -> None:
        """Render completed markdown text with styling if rich is active."""
        if self.is_rich_enabled:
            from rich.markdown import Markdown

            self.console.print(Markdown(text))
        else:
            print(text)

    def render_file_preview(self, path: str, content: str) -> None:
        """Render a syntax-highlighted preview of an expanded @file reference."""
        if self.is_rich_enabled:
            from rich.panel import Panel
            from rich.syntax import Syntax

            ext = os.path.splitext(path)[1].lstrip(".") or "text"
            # Limit preview lines to avoid terminal spam
            lines = content.splitlines()
            preview = "\n".join(lines[:20]) + ("\n..." if len(lines) > 20 else "")
            syntax = Syntax(preview, ext, theme="monokai", line_numbers=True, word_wrap=True)
            self.console.print(
                Panel(
                    syntax,
                    title=f"📄 [bold blue]{path}[/bold blue] ({len(lines)} lines)",
                    border_style="blue",
                    expand=False,
                )
            )
        else:
            print(f"[loaded @{path}]")

    def render_loop_event(self, event: Any, *, verbose: bool) -> None:
        """Render structured agent-loop events with distinct visual styling."""
        event_name = type(event).__name__
        if self.is_rich_enabled:
            if event_name == "PlanEvent":
                label = "🔄 Re-plan" if getattr(event, "is_replan", False) else "📋 Plan"
                self.console.print(
                    f"\n[bold cyan]{label}[/bold cyan] [dim]iteration {event.iteration}:[/dim] "
                    f"[bold]{len(event.plan)} step(s) planned[/bold]"
                )
                if verbose:
                    for i, step in enumerate(event.plan):
                        self.console.print(
                            f"  [dim]step {i + 1}:[/dim] [cyan]{step.get('agent_type')}[/cyan] — {step.get('payload', {})}"
                        )
            elif event_name == "StepStartEvent" and verbose:
                self.console.print(
                    f"  [yellow]⚡ step {event.step_index + 1}[/yellow] running [bold]{event.agent_type}[/bold]…"
                )
            elif event_name == "StepResultEvent" and verbose:
                r = event.result
                duration_str = (
                    f" ({getattr(event, 'duration_seconds', 0.0):.2f}s)"
                    if getattr(event, "duration_seconds", 0.0) > 0.0
                    else ""
                )
                if r and r.success:
                    self.console.print(
                        f"  [green]✓ step {event.step_index + 1}[/green] [dim]succeeded{duration_str}[/dim]"
                    )
                else:
                    err = f": {r.error}" if (r and r.error) else ""
                    self.console.print(
                        f"  [red]✗ step {event.step_index + 1}[/red] [bold red]failed{err}{duration_str}[/bold red]"
                    )
            elif event_name == "ReflectEvent" and verbose:
                self.console.print(
                    f"  [magenta]🔍 reflect[/magenta] [bold]{event.decision}[/bold] [dim]— {event.reason}[/dim]"
                )
            elif event_name == "FinishEvent":
                self.console.print(f"\n[bold green]✨ Done:[/bold green] {event.summary}")
            elif event_name == "LoopErrorEvent":
                self.console.print(f"\n[bold red]❌ Loop Error:[/bold red] {event.error}")

        else:
            # Plain-text fallback
            if event_name == "PlanEvent":
                label = "[re-plan]" if getattr(event, "is_replan", False) else "[plan]"
                print(f"\n{label} iteration {event.iteration}: {len(event.plan)} step(s) planned")
                if verbose:
                    for i, step in enumerate(event.plan):
                        print(
                            f"  step {i + 1}: {step.get('agent_type')} — {step.get('payload', {})}"
                        )
            elif event_name == "StepStartEvent" and verbose:
                print(f"  [step {event.step_index + 1}] running {event.agent_type}…", flush=True)
            elif event_name == "StepResultEvent" and verbose:
                r = event.result
                status = "✓" if (r and r.success) else "✗"
                err = f" ({r.error})" if (r and not r.success and r.error) else ""
                timing = (
                    f" ({getattr(event, 'duration_seconds', 0.0):.2f}s)"
                    if getattr(event, "duration_seconds", 0.0) > 0.0
                    else ""
                )
                print(f"  [step {event.step_index + 1}] {status}{err}{timing}")
            elif event_name == "ReflectEvent" and verbose:
                print(f"  [reflect] {event.decision} — {event.reason}")
            elif event_name == "FinishEvent":
                print(f"\n[done] {event.summary}")
            elif event_name == "LoopErrorEvent":
                print(f"\n[loop-error] {event.error}")

    def render_sessions_table(
        self,
        sessions: list[Any],
        get_stats_fn: Callable[[str], dict[str, Any]],
    ) -> None:
        """Render a formatted table for `agentcli sessions list`."""
        if not sessions:
            print("No saved sessions found.")
            return

        if self.is_rich_enabled:
            from rich.table import Table

            table = Table(
                title="💬 Persisted Sessions", header_style="bold cyan", border_style="dim"
            )

            table.add_column("Session ID", style="bold green", no_wrap=True)
            table.add_column("Updated", style="dim")
            table.add_column("Msgs", justify="right", style="cyan")
            table.add_column("Tokens", justify="right", style="yellow")
            table.add_column("Title", style="white")

            for s in sessions:
                stats = get_stats_fn(s.id)
                msg_count = str(stats.get("message_count", 0))
                tokens = str(stats.get("total_tokens", 0))
                updated_dt = s.updated_at[:19].replace("T", " ")
                table.add_row(s.id, updated_dt, msg_count, tokens, s.title[:35])

            self.console.print(table)
        else:
            print(f"{'SESSION ID':<14} {'UPDATED':<20} {'MSGS':<6} {'TOKENS':<8} {'TITLE'}")
            print("-" * 75)
            for s in sessions:
                stats = get_stats_fn(s.id)
                msg_count = stats["message_count"]
                tokens = stats["total_tokens"]
                updated_dt = s.updated_at[:19].replace("T", " ")
                print(f"{s.id:<14} {updated_dt:<20} {msg_count:<6} {tokens:<8} {s.title[:24]}")

    def render_model_badge(
        self,
        model: str,
        *,
        is_fallback: bool = False,
        requested_primary: str | None = None,
        requested_category: str | None = None,
        served_category: str | None = None,
        show_always: bool = False,
    ) -> None:
        """Render diagnostic model badge after a chat turn."""
        if not model:
            return

        if is_fallback:
            msg = f"[model: {model} — fallback from category {requested_category} to {served_category}]"
        elif requested_primary and model != requested_primary:
            msg = f"[model: {model} — routed from {requested_primary}]"
        elif show_always:
            msg = f"[model: {model}]"
        else:
            return

        if self.is_rich_enabled:
            from rich.text import Text

            self.console.print(Text(msg, style="dim cyan"))
        else:
            print(msg)

    def render_token_usage(
        self,
        usage: dict[str, int] | None,
        *,
        expanded_prompt: str = "",
        full_reply: str = "",
        estimate_fn: Callable[[str], int] | None = None,
    ) -> None:
        """Render token usage diagnostics in verbose mode."""
        if usage:
            p_tok = usage.get("prompt_tokens", 0)
            c_tok = usage.get("completion_tokens", 0)
            t_tok = usage.get("total_tokens", 0)
            msg = f"[tokens: prompt={p_tok}, completion={c_tok}, total={t_tok}]"
            if self.is_rich_enabled:
                from rich.text import Text

                self.console.print(Text(msg, style="dim"))
            else:
                print(msg)
        elif estimate_fn is not None:
            in_tok = estimate_fn(expanded_prompt)
            out_tok = estimate_fn(full_reply)
            tot_tok = in_tok + out_tok
            msg = f"[tokens: prompt~{in_tok}, completion~{out_tok}, total~{tot_tok}]"
            if self.is_rich_enabled:
                from rich.text import Text

                self.console.print(Text(msg, style="dim"))
            else:
                print(msg)
