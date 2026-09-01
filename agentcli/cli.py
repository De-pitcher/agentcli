"""agentcli command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import __version__
from .agent.events import (
    FinishEvent,
    LoopErrorEvent,
    PlanEvent,
    ReflectEvent,
    StepResultEvent,
    StepStartEvent,
)
from .agent.loop import LoopIterationLimitError
from .config import Config, ConfigError, init_config, load_config
from .exit_codes import ExitCode
from .files import FileReadError, expand_file_references
from .memory.budget import estimate_tokens
from .openrouter_client import (
    ChatMessage,
    OpenRouterError,
    RateLimitedError,
)
from .routing.classifier import classify
from .routing.router import NoAvailableModelError
from .session import AgentSession

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentcli",
        description="Budget-conscious, model-agnostic AI agent CLI (OpenRouter-backed).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose (DEBUG) logging")
    parser.add_argument(
        "--preset",
        choices=["coding", "chat", "minimal"],
        help="Apply a workflow preset (e.g. coding, chat, minimal)",
    )
    parser.add_argument(
        "--plugin",
        action="append",
        default=[],
        help="Load a custom tool plugin Python file (repeatable)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    chat_p = sub.add_parser("chat", help="Start an interactive chat session")
    chat_p.add_argument("--model", help="Force a specific model, bypassing automatic routing")
    chat_p.add_argument(
        "--file",
        action="append",
        default=[],
        help="Include a file's contents as context (repeatable)",
    )
    chat_p.add_argument(
        "--no-agents-md",
        action="store_true",
        help="Disable automatic loading of project AGENTS.md instructions",
    )
    chat_p.add_argument(
        "--show-model",
        action="store_true",
        help="Print the model that actually served each reply",
    )
    chat_p.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="Resume a prior conversation session by its ID",
    )

    sub.add_parser(
        "mcp", help="Run agentcli as a Model Context Protocol (MCP) stdio JSON-RPC server"
    )

    sessions_p = sub.add_parser("sessions", help="Manage persisted conversation sessions")
    sessions_sub = sessions_p.add_subparsers(dest="sessions_command", required=True)

    list_p = sessions_sub.add_parser("list", help="List recent conversation sessions")
    list_p.add_argument(
        "--limit", type=int, default=20, help="Maximum sessions to list (default: 20)"
    )

    show_p = sessions_sub.add_parser("show", help="Show message history for a session")
    show_p.add_argument("session_id", help="Session ID to inspect")

    clear_p = sessions_sub.add_parser("clear", help="Clear all stored conversation sessions")
    clear_p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    config_p = sub.add_parser("config", help="Manage agentcli configuration")
    config_sub = config_p.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("init", help="Write a default config file")
    config_sub.add_parser("show", help="Print the resolved configuration")

    return parser


async def run_chat(args: argparse.Namespace, config: Config) -> int:
    forced_model = args.model
    show_model = getattr(args, "show_model", False) or getattr(args, "verbose", False)
    verbose = getattr(args, "verbose", False)

    if not config.routing.enabled and not forced_model:
        forced_model = config.openrouter.default_model

    history: list[ChatMessage] = []

    if args.file:
        try:
            preloaded = expand_file_references(" ".join(f"@{f}" for f in args.file))
        except FileReadError as exc:
            logger.error("%s", exc)
            return ExitCode.CONFIG_ERROR
        history.append(
            ChatMessage(
                role="system",
                content=f"The user has shared the following file(s) for context:\n\n{preloaded}",
            )
        )
        print(f"Loaded {len(args.file)} file(s) into context.")

    resume_id = getattr(args, "resume", None)

    try:
        session = AgentSession(
            config,
            forced_model=forced_model,
            initial_history=history if not resume_id else None,
            session_id=resume_id,
        )
    except OpenRouterError as exc:
        logger.error("%s", exc)
        return ExitCode.CONFIG_ERROR

    if resume_id:
        if session.is_resumed:
            print(f"Resumed session: {session.session_id} ({len(session.history)} messages loaded)")
        else:
            print(f"No session found with ID '{resume_id}'. Starting a new session instead.")

    if session.router is not None:
        print(
            "agentcli — model: auto (task-based routing)  "
            "(Ctrl+C or /exit to quit, end line with \\ for multi-line)"
        )

    else:
        actual_model = forced_model or config.openrouter.default_model
        print(
            f"agentcli — model: {actual_model}  "
            "(Ctrl+C or /exit to quit, end line with \\ for multi-line)"
        )

    interrupted = False
    try:
        while True:
            lines: list[str] = []
            while True:
                prompt = "\nyou> " if not lines else "... "
                try:
                    line = input(prompt)
                except EOFError:
                    break
                except KeyboardInterrupt:
                    interrupted = True
                    break

                if line.endswith("\\"):
                    lines.append(line[:-1])
                    continue
                else:
                    lines.append(line)
                    break

            if interrupted or not lines:
                break

            user_input = "\n".join(lines).strip()
            if not user_input:
                continue
            if user_input in {"/exit", "/quit"}:
                break

            try:
                expanded = expand_file_references(user_input)
            except FileReadError as exc:
                logger.error("%s", exc)
                continue

            # ── AGENTIC LOOP PATH (Phase 4) ────────────────────────────
            if session.should_use_loop(expanded):
                await session.async_add_user_message(expanded)
                if verbose:
                    print("[agent-loop] Multi-step task detected — running Plan→Act→Reflect loop")
                try:
                    async for event in session.run_loop(expanded):
                        _render_loop_event(event, verbose=verbose)
                        if isinstance(event, FinishEvent):
                            loop_summary = event.summary
                        elif isinstance(event, LoopErrorEvent):
                            loop_summary = f"[loop error] {event.error}"
                    await session.async_add_assistant_message(loop_summary or "(loop completed)")
                except LoopIterationLimitError as exc:
                    print(f"\n[agent-loop] Iteration limit reached: {exc}")
                    session.pop_last_message()
                except KeyboardInterrupt:
                    print("\n[interrupted]")
                    session.pop_last_message()
                continue

            # ── SIMPLE SINGLE-TURN CHAT PATH (unchanged from Phase 1/2) ─
            await session.async_add_user_message(expanded)

            print("assistant> ", end="", flush=True)
            reply_parts: list[str] = []

            # Determine routing decision before try block so it's available for error handling
            decision = None
            trimmed = session._trim_history()
            try:
                if session.router is not None:
                    decision = session.router.decide(classify(expanded))
            except NoAvailableModelError as exc:
                print(f"\n[routing error] {exc}")
                session.pop_last_message()
                continue

            requested_primary = decision.primary if decision is not None else None

            try:
                stream = (
                    session.client.chat_stream(trimmed, models=decision.models)
                    if decision is not None
                    else session.client.chat_stream(trimmed, model=forced_model)
                )
                async for delta in stream:
                    print(delta, end="", flush=True)
                    reply_parts.append(delta)

                full_reply = "".join(reply_parts)
                if not full_reply.strip():
                    print("(model returned an empty response)")
                else:
                    print()
                if (show_model or verbose) and session.last_served_model:
                    if decision is not None and decision.is_fallback:
                        print(
                            f"[model: {session.last_served_model} — "
                            f"fallback from category {decision.requested_category} to {decision.served_category}]"
                        )
                    elif requested_primary and session.last_served_model != requested_primary:
                        print(
                            f"[model: {session.last_served_model} — "
                            f"routed from {requested_primary}]"
                        )
                    elif show_model:
                        print(f"[model: {session.last_served_model}]")

                usage = getattr(session.client, "last_usage", {})
                out_tokens = (
                    usage.get("completion_tokens") if usage else estimate_tokens(full_reply)
                )
                if verbose:
                    if usage:
                        print(
                            f"[tokens: prompt={usage.get('prompt_tokens', 0)}, "
                            f"completion={usage.get('completion_tokens', 0)}, "
                            f"total={usage.get('total_tokens', 0)}]"
                        )
                    else:
                        in_tok = estimate_tokens(expanded)
                        out_tok_est = int(out_tokens or 0)
                        print(
                            f"[tokens: prompt~{in_tok}, completion~{out_tok_est}, total~{in_tok + out_tok_est}]"
                        )

                await session.async_add_assistant_message(full_reply, token_count=out_tokens)
                session.mark_success(requested_primary)

            except KeyboardInterrupt:
                print("\n[interrupted]")
                partial = "".join(reply_parts)
                if partial:
                    await session.async_add_assistant_message(partial + "\n[interrupted]")
                else:
                    session.pop_last_message()  # don't poison history with empty turn
                continue
            except OpenRouterError as exc:
                logger.error("%s", exc)
                if requested_primary is not None:
                    session.mark_failure(
                        session.client.last_served_model or requested_primary,
                        exc,
                        rate_limited=isinstance(exc, RateLimitedError),
                    )
                session.pop_last_message()  # don't poison history with a failed turn
                continue

    finally:
        try:
            await session.aclose()
        except (Exception, asyncio.CancelledError, KeyboardInterrupt) as exc:  # noqa: BLE001
            logger.debug("Error closing session: %s", exc)

    if interrupted:
        return ExitCode.USER_INTERRUPT
    return ExitCode.SUCCESS


def _render_loop_event(event: object, *, verbose: bool) -> None:
    """Print a human-readable summary of a LoopEvent.

    Only detailed step/reflect events are shown in verbose mode.
    Plan and finish events are always shown so the user knows progress.
    """
    if isinstance(event, PlanEvent):
        label = "[re-plan]" if event.is_replan else "[plan]"
        print(f"\n{label} iteration {event.iteration}: {len(event.plan)} step(s) planned")
        if verbose:
            for i, step in enumerate(event.plan):
                print(f"  step {i + 1}: {step.get('agent_type')} — {step.get('payload', {})}")
    elif isinstance(event, StepStartEvent) and verbose:
        print(f"  [step {event.step_index + 1}] running {event.agent_type}…", flush=True)
    elif isinstance(event, StepResultEvent) and verbose:
        r = event.result
        status = "✓" if (r and r.success) else "✗"
        err = f" ({r.error})" if (r and not r.success and r.error) else ""
        print(f"  [step {event.step_index + 1}] {status}{err}")
    elif isinstance(event, ReflectEvent) and verbose:
        print(f"  [reflect] {event.decision} — {event.reason}")
    elif isinstance(event, FinishEvent):
        print(f"\n[done] {event.summary}")
    elif isinstance(event, LoopErrorEvent):
        print(f"\n[loop-error] {event.error}")


def run_sessions(args: argparse.Namespace, config: Config) -> int:
    from .memory.store import MemoryStore

    if not config.memory.enabled:
        print("Memory persistence is disabled in config ([memory] enabled = false).")
        return ExitCode.SUCCESS

    store = MemoryStore(config.memory.db_path or None)
    try:
        if args.sessions_command == "list":
            sessions = store.list_sessions(limit=args.limit)
            if not sessions:
                print("No saved sessions found.")
                return ExitCode.SUCCESS
            print(f"{'SESSION ID':<14} {'UPDATED':<20} {'MSGS':<6} {'TOKENS':<8} {'TITLE'}")
            print("-" * 75)
            for s in sessions:
                stats = store.get_session_stats(s.id)
                msg_count = stats["message_count"]
                tokens = stats["total_tokens"]
                updated_dt = s.updated_at[:19].replace("T", " ")
                print(f"{s.id:<14} {updated_dt:<20} {msg_count:<6} {tokens:<8} {s.title[:24]}")
            return ExitCode.SUCCESS

        if args.sessions_command == "show":
            session = store.get_session(args.session_id)
            if not session:
                logger.error("Session '%s' not found.", args.session_id)
                return ExitCode.GENERAL_ERROR
            stats = store.get_session_stats(args.session_id)
            print(f"Session ID: {session.id}")
            print(f"Title:      {session.title}")
            print(f"Created:    {session.created_at[:19].replace('T', ' ')}")
            print(f"Updated:    {session.updated_at[:19].replace('T', ' ')}")
            print(f"Messages:   {stats['message_count']}")
            print(
                f"Token Usage: {stats['total_tokens']} total "
                f"({stats['user_tokens']} prompt, {stats['assistant_tokens']} completion)"
            )
            print("Est. Cost:    $0.0000 (free tier models)")
            print("-" * 75)
            messages = store.get_messages(args.session_id)
            if not messages:
                print("(No messages in session)")
            for msg in messages:
                role_label = msg.role.upper()
                time_label = msg.created_at[:19].replace("T", " ")
                tok_info = f" [{msg.token_count} tokens]" if msg.token_count is not None else ""
                print(f"\n[{role_label}] ({time_label}){tok_info}")
                print(msg.content)
            return ExitCode.SUCCESS

        if args.sessions_command == "clear":
            if not args.yes:
                try:
                    confirm = (
                        input("Are you sure you want to delete all saved sessions? [y/N]: ")
                        .strip()
                        .lower()
                    )
                except (EOFError, KeyboardInterrupt):
                    print("\nAborted.")
                    return ExitCode.SUCCESS
                if confirm not in ("y", "yes"):
                    print("Aborted.")
                    return ExitCode.SUCCESS
            count = store.clear_all_sessions()
            print(f"Cleared {count} stored session(s).")
            return ExitCode.SUCCESS
    finally:
        store.close()
    return ExitCode.GENERAL_ERROR


def run_config(args: argparse.Namespace, config: Config) -> int:
    if args.config_command == "init":
        path, written = init_config()
        if written:
            print(f"Wrote default config to {path}")
        else:
            print(f"Config file already exists at {path} — leaving it untouched.")
        return ExitCode.SUCCESS
    if args.config_command == "show":
        print(f"openrouter.api_key_env  = {config.openrouter.api_key_env}")
        print(f"openrouter.default_model = {config.openrouter.default_model}")
        print(f"openrouter.timeout_seconds = {config.openrouter.timeout_seconds}")
        print(f"openrouter.max_retries  = {config.openrouter.max_retries}")
        print(f"openrouter.base_url     = {config.openrouter.base_url}")
        print(f"app.stream              = {config.app.stream}")
        print(f"app.history_turns       = {config.app.history_turns}")
        print(f"memory.enabled          = {config.memory.enabled}")
        print(f"memory.db_path          = {config.memory.db_path or '(default platform path)'}")
        print(f"memory.retention_days   = {config.memory.retention_days}")
        print(f"memory.cache_enabled    = {config.memory.cache_enabled}")
        print(f"memory.max_shared_bytes = {config.memory.max_shared_context_bytes}")
        print(f"memory.budget_ratio     = {config.memory.budget_ratio}")
        print(f"memory.max_cache_entries = {config.memory.max_cache_entries}")
        print(f"memory.max_cache_bytes  = {config.memory.max_cache_bytes}")
        return ExitCode.SUCCESS
    return ExitCode.GENERAL_ERROR


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        config = load_config(preset=getattr(args, "preset", None))
    except ConfigError as exc:
        logger.error(str(exc))
        return ExitCode.CONFIG_ERROR

    if getattr(args, "plugin", None):
        config.app.plugins.extend(args.plugin)

    if getattr(args, "no_agents_md", False):
        config.app.load_agents_md = False

    if args.command == "mcp":
        from .agent.registry import ToolRegistry
        from .mcp import run_mcp

        reg = ToolRegistry()
        for p in config.app.plugins:
            reg.load_plugin_file(p)
        return run_mcp(registry=reg)

    if args.command == "chat":
        try:
            return asyncio.run(run_chat(args, config))
        except KeyboardInterrupt:
            # Real SIGINT can surface here (e.g. delivered during async cleanup
            # on Windows) after the REPL already handled the first press.
            return ExitCode.USER_INTERRUPT
    if args.command == "sessions":
        return run_sessions(args, config)
    if args.command == "config":
        return run_config(args, config)

    parser.print_help()
    return ExitCode.GENERAL_ERROR


if __name__ == "__main__":
    sys.exit(main())
