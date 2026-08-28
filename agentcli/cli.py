"""agentcli command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import httpx

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
from .openrouter_client import (
    ChatMessage,
    OpenRouterError,
    RateLimitedError,
)
from .routing.classifier import classify
from .session import AgentSession

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentcli",
        description="Budget-conscious, model-agnostic AI agent CLI (OpenRouter-backed).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose (DEBUG) logging")

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
        "--show-model",
        action="store_true",
        help="Print the model that actually served each reply",
    )

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

    try:
        session = AgentSession(config, forced_model=forced_model, initial_history=history)
    except OpenRouterError as exc:
        logger.error("%s", exc)
        return ExitCode.CONFIG_ERROR

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
                session.add_user_message(expanded)
                if verbose:
                    print("[agent-loop] Multi-step task detected — running Plan→Act→Reflect loop")
                try:
                    event_stream = await session.run_loop(expanded)
                    loop_summary = ""
                    async for event in event_stream:
                        _render_loop_event(event, verbose=verbose)
                        if isinstance(event, FinishEvent):
                            loop_summary = event.summary
                        elif isinstance(event, LoopErrorEvent):
                            loop_summary = f"[loop error] {event.error}"
                    session.add_assistant_message(loop_summary or "(loop completed)")
                except LoopIterationLimitError as exc:
                    print(f"\n[agent-loop] Iteration limit reached: {exc}")
                    session.pop_last_message()
                except KeyboardInterrupt:
                    print("\n[interrupted]")
                    session.pop_last_message()
                continue

            # ── SIMPLE SINGLE-TURN CHAT PATH (unchanged from Phase 1/2) ─
            session.add_user_message(expanded)

            print("assistant> ", end="", flush=True)
            reply_parts: list[str] = []

            # Determine routing decision before try block so it's available for error handling
            decision = None
            trimmed = session._trim_history()
            if session.router is not None:
                decision = session.router.decide(classify(expanded))
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
                if show_model and session.last_served_model:
                    if requested_primary and session.last_served_model != requested_primary:
                        print(
                            f"[model: {session.last_served_model} — "
                            f"routed from {requested_primary}]"
                        )
                    else:
                        print(f"[model: {session.last_served_model}]")
                session.add_assistant_message(full_reply)
                session.mark_success(requested_primary)

            except KeyboardInterrupt:
                print("\n[interrupted]")
                partial = "".join(reply_parts)
                if partial:
                    session.add_assistant_message(partial + "\n[interrupted]")
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
        except (asyncio.CancelledError, KeyboardInterrupt, httpx.HTTPError) as exc:
            logger.debug("Client close skipped: %s", exc)
        print("\n(exiting)")

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
        config = load_config()
    except ConfigError as exc:
        logger.error(str(exc))
        return ExitCode.CONFIG_ERROR

    if args.command == "chat":
        try:
            return asyncio.run(run_chat(args, config))
        except KeyboardInterrupt:
            # Real SIGINT can surface here (e.g. delivered during async cleanup
            # on Windows) after the REPL already handled the first press.
            return ExitCode.USER_INTERRUPT
    if args.command == "config":
        return run_config(args, config)

    parser.print_help()
    return ExitCode.GENERAL_ERROR


if __name__ == "__main__":
    sys.exit(main())
