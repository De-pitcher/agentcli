"""agentcli command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

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
from .files import FileReadError, expand_file_references, load_agents_md
from .memory.budget import estimate_tokens
from .openrouter_client import (
    ChatMessage,
    OpenRouterError,
    RateLimitedError,
)
from .routing.classifier import classify
from .routing.router import NoAvailableModelError, Router
from .session import AgentSession
from .ui.prompt import InteractivePrompt
from .ui.render import ConsoleRenderer
from .unicode import safe_print

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose (DEBUG) logging"
    )
    common_parser.add_argument(
        "--plain",
        action="store_true",
        help="Disable rich formatting and interactive prompts",
    )
    common_parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color output",
    )
    common_parser.add_argument(
        "--preset",
        choices=["coding", "chat", "minimal"],
        help="Apply a workflow preset (e.g. coding, chat, minimal)",
    )
    common_parser.add_argument(
        "--plugin",
        action="append",
        default=[],
        help="Load a custom tool plugin Python file (repeatable)",
    )

    sub_common_parser = argparse.ArgumentParser(add_help=False)
    sub_common_parser.add_argument(
        "--verbose", action="store_true", default=argparse.SUPPRESS, help="Enable verbose (DEBUG) logging"
    )
    sub_common_parser.add_argument(
        "--plain",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Disable rich formatting and interactive prompts",
    )
    sub_common_parser.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Disable ANSI color output",
    )
    sub_common_parser.add_argument(
        "--preset",
        choices=["coding", "chat", "minimal"],
        default=argparse.SUPPRESS,
        help="Apply a workflow preset (e.g. coding, chat, minimal)",
    )
    sub_common_parser.add_argument(
        "--plugin",
        action="append",
        default=argparse.SUPPRESS,
        help="Load a custom tool plugin Python file (repeatable)",
    )

    parser = argparse.ArgumentParser(
        prog="agentcli",
        description="Budget-conscious, model-agnostic AI agent CLI (OpenRouter-backed).",
        parents=[common_parser],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    chat_p = sub.add_parser(
        "chat", help="Start an interactive chat session", parents=[sub_common_parser]
    )
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
    chat_p.add_argument(
        "--allow-write",
        action="store_true",
        help="Permit mutating file operations (write, create, delete, mkdir)",
    )
    chat_p.add_argument(
        "--budget",
        choices=["low", "medium", "high"],
        default=None,
        help="Budget tier for model selection (low=free/fast, medium=balanced, high=frontier)",
    )
    chat_p.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Maximum cumulative session cost budget in USD",
    )

    run_p = sub.add_parser(
        "run", help="Autonomously execute a multi-turn goal", parents=[sub_common_parser]
    )
    run_p.add_argument("goal", help="The goal or task description to accomplish")
    run_p.add_argument("--model", help="Force a specific model, bypassing automatic routing")
    run_p.add_argument(
        "--file",
        action="append",
        default=[],
        help="Include a file's contents as context (repeatable)",
    )
    run_p.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Maximum agent loop iterations (default: 5 or config)",
    )
    run_p.add_argument(
        "--no-agents-md",
        action="store_true",
        help="Disable automatic loading of project AGENTS.md instructions",
    )
    run_p.add_argument(
        "--allow-write",
        action="store_true",
        help="Permit mutating file operations (write, create, delete, mkdir)",
    )
    run_p.add_argument(
        "--budget",
        choices=["low", "medium", "high"],
        default=None,
        help="Budget tier for model selection (low=free/fast, medium=balanced, high=frontier)",
    )
    run_p.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Maximum cumulative execution cost budget in USD",
    )

    mcp_p = sub.add_parser(
        "mcp",
        help="Run agentcli as a Model Context Protocol (MCP) stdio JSON-RPC server",
        parents=[sub_common_parser],
    )
    mcp_p.add_argument(
        "--allow-write",
        action="store_true",
        help="Permit mutating file operations (write, create, delete, mkdir)",
    )

    sessions_p = sub.add_parser(
        "sessions", help="Manage persisted conversation sessions", parents=[sub_common_parser]
    )
    sessions_sub = sessions_p.add_subparsers(dest="sessions_command", required=True)

    list_p = sessions_sub.add_parser(
        "list", help="List recent conversation sessions", parents=[sub_common_parser]
    )
    list_p.add_argument(
        "--limit", type=int, default=20, help="Maximum sessions to list (default: 20)"
    )

    show_p = sessions_sub.add_parser(
        "show", help="Show message history for a session", parents=[sub_common_parser]
    )
    show_p.add_argument("session_id", help="Session ID to inspect")

    clear_p = sessions_sub.add_parser(
        "clear", help="Clear all stored conversation sessions", parents=[sub_common_parser]
    )
    clear_p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    config_p = sub.add_parser(
        "config", help="Manage agentcli configuration", parents=[sub_common_parser]
    )
    config_sub = config_p.add_subparsers(dest="config_command", required=True)
    init_p = config_sub.add_parser(
        "init", help="Write a default config file", parents=[sub_common_parser]
    )
    init_p.add_argument(
        "--local",
        action="store_true",
        help="Write config to agentcli.toml in the current working directory",
    )
    config_sub.add_parser(
        "show", help="Print the resolved configuration", parents=[sub_common_parser]
    )

    return parser


async def run_chat(args: argparse.Namespace, config: Config) -> int:
    if getattr(args, "budget", None):
        config.routing.budget_tier = args.budget
    if getattr(args, "max_cost", None) is not None:
        config.routing.max_cost_usd = args.max_cost

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

    plain = getattr(args, "plain", False)
    no_color = getattr(args, "no_color", False)
    renderer = ConsoleRenderer(plain=plain, no_color=no_color)
    interactive_prompt = InteractivePrompt(plain=plain)

    await session.initialize_mcp()

    ws_summary = await session.auto_ground_workspace()
    if ws_summary and (verbose or show_model):
        safe_print(f"[workspace] {ws_summary}")

    if session.router is not None:
        print("agentcli -- model: auto (task-based routing)  (Ctrl+C or /exit to quit)")
    else:
        actual_model = forced_model or config.openrouter.default_model
        print(f"agentcli -- model: {actual_model}  (Ctrl+C or /exit to quit)")

    interrupted = False
    try:
        while True:
            try:
                user_input = (await interactive_prompt.get_input_async("you> ")).strip()
            except EOFError:
                break
            except KeyboardInterrupt:
                interrupted = True
                break

            if not user_input:
                continue
            if user_input in {"/exit", "/quit"}:
                break

            # --- IN-SESSION SLASH COMMANDS (Phase 18) ---------------------
            if user_input == "/help":
                if renderer.is_rich_enabled:
                    renderer.console.print(
                        "\n[bold cyan]Available Slash Commands:[/bold cyan]\n"
                        "  [bold]/help[/bold]                 Show this help message\n"
                        "  [bold]/budget [tier][/bold]        View or set budget tier ([green]low[/green], [yellow]medium[/yellow], [red]high[/red])\n"
                        "  [bold]/model [model|auto][/bold]   View or switch active model (or return to auto-routing)\n"
                        "  [bold]/goal <description>[/bold]   Run an autonomous multi-step goal loop directly in chat\n"
                        "  [bold]/tokens[/bold]               Show current session token usage breakdown\n"
                        "  [bold]/cost[/bold]                 Show current session estimated cost in USD\n"
                        "  [bold]/clear[/bold]                Clear terminal screen\n"
                        "  [bold]/reset[/bold]                Reset conversation history and start fresh\n"
                        "  [bold]/exit, /quit[/bold]          Exit agentcli\n"
                    )
                else:
                    print(
                        "\nAvailable Slash Commands:\n"
                        "  /help                 Show this help message\n"
                        "  /budget [tier]        View or set budget tier (low, medium, high)\n"
                        "  /model [model|auto]   View or switch active model (or auto routing)\n"
                        "  /goal <description>   Run an autonomous multi-step goal loop\n"
                        "  /tokens               Show current session token usage breakdown\n"
                        "  /cost                 Show current session estimated cost in USD\n"
                        "  /clear                Clear terminal screen\n"
                        "  /reset                Reset conversation history and start fresh\n"
                        "  /exit, /quit          Exit agentcli\n"
                    )
                continue

            if user_input.startswith("/budget"):
                parts = user_input.split(maxsplit=1)
                if len(parts) == 1:
                    current_tier = config.routing.budget_tier
                    print(f"Current budget tier: {current_tier}")
                else:
                    tier = parts[1].strip().lower()
                    if tier in {"low", "medium", "high"}:
                        config.routing.budget_tier = tier
                        if session.router is not None:
                            session.router._budget_tier = tier
                        print(f"Budget tier updated to: {tier}")
                    else:
                        print(f"Invalid budget tier '{tier}'. Choose from: low, medium, high")
                continue

            if user_input.startswith("/model"):
                parts = user_input.split(maxsplit=1)
                if len(parts) == 1:
                    active = forced_model or "auto (task-based routing)"
                    print(f"Current model: {active}")
                else:
                    target_model = parts[1].strip()
                    if target_model.lower() == "auto":
                        forced_model = None
                        session.forced_model = None
                        if session.registry is not None:
                            session.router = Router(
                                session.registry,
                                config.routing.max_fallbacks,
                                budget_tier=config.routing.budget_tier,
                            )
                        print("Switched to auto model routing.")
                    else:
                        forced_model = target_model
                        session.forced_model = target_model
                        session.router = None
                        print(f"Forced model set to: {target_model}")
                continue

            if user_input in {"/tokens", "/cost"}:
                stats = await session.get_session_stats()
                cost = session.cumulative_cost_usd
                print(
                    f"Token Usage: {stats['total_tokens']} total "
                    f"({stats['user_tokens']} prompt, {stats['assistant_tokens']} completion)"
                )
                print(f"Estimated Cost: ${cost:.6f} USD")
                continue

            if user_input == "/clear":
                renderer.clear()
                continue

            if user_input == "/reset":
                session.history.clear()
                await session.auto_ground_workspace()
                print("Session reset. Conversation history cleared.")
                continue

            if user_input.startswith("/goal"):
                parts = user_input.split(maxsplit=1)
                if len(parts) < 2 or not parts[1].strip():
                    print("Usage: /goal <task description>")
                    continue
                goal_text = parts[1].strip()
                loop_summary: str | None = None
                try:
                    with renderer.status_spinner("Running autonomous goal loop..."):
                        async for event in session.run_loop(goal_text):
                            renderer.render_loop_event(event, verbose=verbose)
                            if isinstance(event, FinishEvent):
                                loop_summary = event.summary
                                if getattr(event, "output", None):
                                    loop_summary = f"{event.summary}\n\n{event.output}"
                            elif isinstance(event, LoopErrorEvent):
                                loop_summary = f"[loop error] {event.error}"
                    await session.async_add_assistant_message(
                        f"[Autonomous Goal: {goal_text}]\n{loop_summary or '(completed)'}"
                    )
                except LoopIterationLimitError as exc:
                    print(f"\n[agent-loop] Iteration limit reached: {exc}")
                except KeyboardInterrupt:
                    print("\n[interrupted]")
                continue

            try:
                expanded = expand_file_references(user_input)
            except FileReadError as exc:
                logger.error("%s", exc)
                continue

            # --- AGENTIC LOOP PATH (Phase 4) ----------------------------
            if session.should_use_loop(expanded):
                await session.async_add_user_message(expanded)
                if verbose:
                    safe_print(
                        "[agent-loop] Multi-step task detected -- running Plan->Act->Reflect loop"
                    )
                try:
                    with renderer.status_spinner("Thinking and planning steps..."):
                        async for event in session.run_loop(expanded):
                            renderer.render_loop_event(event, verbose=verbose)
                            if isinstance(event, FinishEvent):
                                if getattr(event, "output", None):
                                    loop_summary = f"{event.summary}\n\n{event.output}"
                                else:
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

            # --- SIMPLE SINGLE-TURN CHAT PATH (unchanged from Phase 1/2) --
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
                    try:
                        print(delta, end="", flush=True)
                    except UnicodeEncodeError:
                        safe_print(delta, end="", flush=True)
                    reply_parts.append(delta)

                full_reply = "".join(reply_parts)
                if not full_reply.strip():
                    print("(model returned an empty response)")
                else:
                    print()
                if (show_model or verbose) and session.last_served_model:
                    if decision is not None and decision.is_fallback:
                        safe_print(
                            f"[model: {session.last_served_model} -- "
                            f"fallback from category {decision.requested_category} to {decision.served_category}]"
                        )
                    elif requested_primary and session.last_served_model != requested_primary:
                        safe_print(
                            f"[model: {session.last_served_model} -- "
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


async def run_goal(args: argparse.Namespace, config: Config) -> int:
    """Execute an autonomous goal-driven task using AgentLoop."""
    goal: str = getattr(args, "goal", "")
    forced_model: str | None = getattr(args, "model", None)
    if getattr(args, "budget", None):
        config.routing.budget_tier = args.budget
    if getattr(args, "max_cost", None) is not None:
        config.routing.max_cost_usd = args.max_cost

    allow_write: bool = getattr(args, "allow_write", False)
    if allow_write:
        config.subagents.allow_write = True

    renderer = ConsoleRenderer(
        plain=getattr(args, "plain", False),
        no_color=getattr(args, "no_color", False),
    )

    # 1. Load project instructions (AGENTS.md) if enabled
    initial_context_parts: list[str] = []
    if not getattr(args, "no_agents_md", False):
        agents_context = load_agents_md()
        if agents_context:
            initial_context_parts.append(agents_context)

    # 2. Expand any explicit --file arguments
    for file_ref in getattr(args, "file", []):
        try:
            content = expand_file_references(f"@{file_ref}")
            initial_context_parts.append(content)
        except FileReadError as err:
            logger.error("Error reading file context %s: %s", file_ref, err)
            return ExitCode.GENERAL_ERROR

    # 3. Auto-ground workspace git status if in a git repo
    session = AgentSession(config=config)
    try:
        await session.initialize_mcp()
        git_summary = await session.auto_ground_workspace()
        if git_summary:
            initial_context_parts.append(f"Workspace Context: {git_summary}")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Workspace grounding or MCP init skipped: %s", exc)

    initial_context = "\n\n".join(initial_context_parts) if initial_context_parts else None

    # 4. Build registry & load plugins
    from .agent.loop import AgentLoop, LoopIterationLimitError
    from .agent.registry import ToolRegistry
    from .routing.router import Router

    registry = ToolRegistry(config=config)
    session.mcp_manager.register_tools(registry)
    for p in config.app.plugins:
        registry.load_plugin_file(p)

    router: Router | None = None
    if config.routing.enabled and not forced_model:
        from .routing.registry import ModelRegistry

        model_registry = ModelRegistry(config.routing)
        router = Router(
            model_registry,
            config.routing.max_fallbacks,
            budget_tier=config.routing.budget_tier,
        )

    raw_max_iter = getattr(args, "max_iterations", None) or getattr(
        config.agent_loop, "max_iterations", 5
    )
    max_iterations: int = int(raw_max_iter) if raw_max_iter is not None else 5
    max_cost_usd = getattr(args, "max_cost", None) or getattr(
        config.routing, "max_cost_usd", None
    )

    if renderer.is_rich_enabled:
        renderer.console.print(f"[bold cyan]🎯 Goal:[/bold cyan] [bold]{goal}[/bold]")
        if forced_model:
            renderer.console.print(
                f"[dim]Model: {forced_model} | Max Iterations: {max_iterations}[/dim]\n"
            )
        else:
            budget_label = config.routing.budget_tier.upper()
            renderer.console.print(
                f"[dim]Autonomous Router ({budget_label} budget) | Max Iterations: {max_iterations}[/dim]\n"
            )
    else:
        print(f"Goal: {goal}")
        print(f"Max iterations: {max_iterations}\n")

    loop = AgentLoop(
        goal=goal,
        registry=registry,
        router=router,
        max_iterations=max_iterations,
        plan_model=forced_model,
        reflect_model=forced_model,
        config=config,
        initial_context=initial_context,
        max_cost_usd=max_cost_usd,
    )

    try:
        async for event in loop.run():
            renderer.render_loop_event(event, verbose=True)
            if isinstance(event, FinishEvent):
                return ExitCode.SUCCESS
            if isinstance(event, LoopErrorEvent):
                logger.error("Loop failed: %s", event.error)
                return ExitCode.GENERAL_ERROR
    except LoopIterationLimitError as exc:
        if renderer.is_rich_enabled:
            renderer.console.print(f"[bold red]❌ Limit Reached:[/bold red] {exc}")
        else:
            print(f"[limit-reached] {exc}")
        return ExitCode.GENERAL_ERROR
    except RateLimitedError as exc:
        logger.error("Rate limit encountered: %s", exc)
        return ExitCode.GENERAL_ERROR
    except OpenRouterError as exc:
        logger.error("Model API error: %s", exc)
        return ExitCode.GENERAL_ERROR
    except KeyboardInterrupt:
        if renderer.is_rich_enabled:
            renderer.console.print("\n[yellow]⚠️ Interrupted by user[/yellow]")
        else:
            print("\n[interrupted]")
        return ExitCode.USER_INTERRUPT
    except Exception as exc:  # noqa: BLE001
        logger.error("Execution failed: %s", exc)
        return ExitCode.GENERAL_ERROR

    return ExitCode.SUCCESS


def _render_loop_event(event: object, *, verbose: bool) -> None:
    """Print a human-readable summary of a LoopEvent.

    Only detailed step/reflect events are shown in verbose mode.
    Plan and finish events are always shown so the user knows progress.
    """
    if isinstance(event, PlanEvent):
        label = "[re-plan]" if event.is_replan else "[plan]"
        safe_print(f"\n{label} iteration {event.iteration}: {len(event.plan)} step(s) planned")
        if verbose:
            for i, step in enumerate(event.plan):
                safe_print(f"  step {i + 1}: {step.get('agent_type')} -- {step.get('payload', {})}")
    elif isinstance(event, StepStartEvent) and verbose:
        safe_print(f"  [step {event.step_index + 1}] running {event.agent_type}...", flush=True)
    elif isinstance(event, StepResultEvent) and verbose:
        r = event.result
        status = "[OK]" if (r and r.success) else "[FAIL]"
        err = f" ({r.error})" if (r and not r.success and r.error) else ""
        timing = (
            f" ({event.duration_seconds:.2f}s)"
            if getattr(event, "duration_seconds", 0.0) > 0.0
            else ""
        )
        safe_print(f"  [step {event.step_index + 1}] {status}{err}{timing}")
    elif isinstance(event, ReflectEvent) and verbose:
        safe_print(f"  [reflect] {event.decision} -- {event.reason}")
    elif isinstance(event, FinishEvent):
        safe_print(f"\n[done] {event.summary}")
        if getattr(event, "output", None):
            safe_print(f"\n{event.output}\n")
    elif isinstance(event, LoopErrorEvent):
        safe_print(f"\n[loop-error] {event.error}")


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
        target = Path("agentcli.toml") if getattr(args, "local", False) else None
        path, written = init_config(path=target)
        if written:
            print(f"Wrote default config to {path}")
        else:
            print(f"Config file already exists at {path} -- leaving it untouched.")
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

    if getattr(args, "allow_write", False):
        config.subagents.allow_write = True

    if args.command == "mcp":
        from .agent.registry import ToolRegistry
        from .mcp import run_mcp

        reg = ToolRegistry(config=config)
        for p in config.app.plugins:
            reg.load_plugin_file(p)
        return run_mcp(registry=reg)

    if args.command == "run":
        try:
            return asyncio.run(run_goal(args, config))
        except KeyboardInterrupt:
            return ExitCode.USER_INTERRUPT
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
