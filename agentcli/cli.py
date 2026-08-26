"""agentcli command-line entry point."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import __version__
from .config import Config, init_config, load_config
from .exit_codes import ExitCode
from .files import FileReadError, expand_file_references
from .openrouter_client import ChatMessage, OpenRouterClient, OpenRouterError

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentcli",
        description="Budget-conscious, model-agnostic AI agent CLI (OpenRouter-backed).",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose (DEBUG) logging"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    chat_p = sub.add_parser("chat", help="Start an interactive chat session")
    chat_p.add_argument("--model", help="Override the configured default model")
    chat_p.add_argument(
        "--file", action="append", default=[],
        help="Include a file's contents as context (repeatable)",
    )

    config_p = sub.add_parser("config", help="Manage agentcli configuration")
    config_sub = config_p.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("init", help="Write a default config file")
    config_sub.add_parser("show", help="Print the resolved configuration")

    return parser


async def run_chat(args: argparse.Namespace, config: Config) -> int:
    try:
        client = OpenRouterClient(config.openrouter)
    except OpenRouterError as exc:
        logger.error("%s", exc)
        return ExitCode.CONFIG_ERROR

    model = args.model or config.openrouter.default_model
    history: list[ChatMessage] = []

    if args.file:
        try:
            preloaded = expand_file_references(" ".join(f"@{f}" for f in args.file))
        except FileReadError as exc:
            logger.error("%s", exc)
            await client.aclose()
            return ExitCode.CONFIG_ERROR
        history.append(ChatMessage(
            role="system",
            content=f"The user has shared the following file(s) for context:\n\n{preloaded}",
        ))
        print(f"Loaded {len(args.file)} file(s) into context.")

    print(f"agentcli — model: {model}  (Ctrl+C or /exit to quit, end line with \\ for multi-line)")

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

            history.append(ChatMessage(role="user", content=expanded))

            if history and history[0].role == "system":
                # Preserve system message, trim the rest to (turns * 2) previous messages + 1 current message
                trimmed = [history[0]] + history[1:][-(config.app.history_turns * 2 + 1):]
            else:
                trimmed = history[-(config.app.history_turns * 2 + 1):]

            print("assistant> ", end="", flush=True)
            reply_parts: list[str] = []
            try:
                async for delta in client.chat_stream(trimmed, model=model):
                    print(delta, end="", flush=True)
                    reply_parts.append(delta)

                full_reply = "".join(reply_parts)
                if not full_reply.strip():
                    print("(model returned an empty response)")
                else:
                    print()
                history.append(ChatMessage(role="assistant", content=full_reply))
            except KeyboardInterrupt:
                print("\n[interrupted]")
                partial = "".join(reply_parts)
                if partial:
                    history.append(ChatMessage(role="assistant", content=partial + "\n[interrupted]"))
                else:
                    history.pop()  # don't poison history with empty turn
                continue
            except OpenRouterError as exc:
                logger.error("%s", exc)
                history.pop()  # don't poison history with a failed turn
                continue

    finally:
        await client.aclose()
        print("\n(exiting)")

    if interrupted:
        return ExitCode.USER_INTERRUPT
    return ExitCode.SUCCESS


def run_config(args: argparse.Namespace, config: Config) -> int:
    if args.config_command == "init":
        path = init_config()
        print(f"Wrote default config to {path}")
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
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s"
    )

    config = load_config()

    if args.command == "chat":
        return asyncio.run(run_chat(args, config))
    if args.command == "config":
        return run_config(args, config)

    parser.print_help()
    return ExitCode.GENERAL_ERROR


if __name__ == "__main__":
    sys.exit(main())
