"""Private fixed-operation helper. No provider credentials or arbitrary commands."""

from __future__ import annotations

import json
import socket
import sqlite3
import sys
from pathlib import Path
from typing import Any

# Verified foundation helper operations:
# resolve (handled in execute)
# path, authorize, read_text, walk, capture, fingerprint, restore, display_diff
# file_ops (gated on hasattr(agent, "_run_local"))
ALLOWED_OPERATIONS = frozenset(
    {
        "path",
        "authorize",
        "read_text",
        "walk",
        "capture",
        "fingerprint",
        "restore",
        "display_diff",
        "file_ops",
        "shell_execution",
        "workspace_list_tree",
    }
)


def execute(request: dict[str, Any]) -> Any:
    if request.get("operation") != "resolve":
        return filesystem(request)
    host, port = request.get("host"), request.get("port")
    if not isinstance(host, str) or not 0 < len(host) <= 253:
        raise ValueError("Invalid resolver hostname")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port <= 65535:
        raise ValueError("Invalid resolver port")
    rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not rows or len(rows) > 64:
        raise ValueError("DNS address count exceeds supported limits")
    return [str(row[4][0]) for row in rows]


def filesystem(request: dict[str, Any]) -> Any:
    from . import io_boundary
    from .policy import RunPolicy

    io_boundary.in_worker = True
    operation = request["operation"]
    if operation not in ALLOWED_OPERATIONS:
        raise NotImplementedError(
            f"Helper operation '{operation}' is unsupported on this foundation runtime"
        )

    data = request["policy"]
    data["root"] = Path(data["root"])
    data["readable_roots"] = tuple(Path(p) for p in data["readable_roots"])
    policy = RunPolicy(**data)

    with policy.activate():
        if operation == "path":
            return str(policy.path(request["path"], write=request.get("write", False)))
        if operation == "authorize":
            policy.authorize(request["name"], request["payload"])
            return None
        if operation == "read_text":
            from .bounds import read_text

            return read_text(request["path"], policy, request.get("encoding", "utf-8"))
        if operation == "walk":
            from .bounds import walk_entries

            entries = []
            error = None
            try:
                for p, depth, directory in walk_entries(
                    Path(request["path"]),
                    policy,
                    set(request["excludes"]),
                    max_entries=request["max_entries"],
                    max_depth=request["max_depth"],
                ):
                    entries.append([str(p), depth, directory])
            except ValueError as exc:
                error = str(exc)
            return {"entries": entries, "error": error}
        if operation in {"capture", "fingerprint", "restore"}:
            from .agent.checkpoints import capture, fingerprint, restore

            if operation == "capture":
                return io_boundary.pack_snapshot(capture(policy, request["path"]))
            if operation == "fingerprint":
                return fingerprint(policy.path(request["path"]))
            restore(policy, io_boundary.unpack_snapshot(request["snapshot"]))
            return None
        if operation == "display_diff":
            from .agent.checkpoints import CheckpointManager

            journal = CheckpointManager(policy=policy)
            journal.create_checkpoint()
            snapshot = io_boundary.unpack_snapshot(request["snapshot"])
            journal.record_snapshot(snapshot)
            return journal.get_diff()
        if operation == "file_ops":
            import asyncio

            from .subagents.base import SubAgentTask, SubAgentType
            from .subagents.file_ops import FileOpsAgent

            policy.authorize("file_ops", request["payload"])
            agent = FileOpsAgent(
                config={
                    "working_dir": str(policy.root),
                    "allow_write": policy.allow_write,
                    "read_only": request["read_only"],
                }
            )
            if not hasattr(agent, "_run_local"):
                raise NotImplementedError(
                    "FileOps in-worker local execution is unavailable on this runtime base"
                )
            result = asyncio.run(
                agent._run_local(
                    SubAgentTask(
                        id=request["task_id"],
                        agent_type=SubAgentType.FILE_OPS,
                        payload=request["payload"],
                    )
                )
            )
            return {"success": result.success, "output": result.output, "error": result.error}
        if operation == "shell_execution":
            import asyncio
            import shutil

            from .processes import run_process
            from .subagents.shell import ShellExecutionAgent

            payload = request.get("payload", {})
            command = payload.get("command", "").strip()
            working_dir = policy.path(payload.get("working_dir") or policy.root)
            timeout = payload.get("timeout", policy.timeout_seconds)

            policy.command(command, working_dir)

            caller_cfg = request.get("config", {})
            if not isinstance(caller_cfg, dict):
                caller_cfg = {}

            shell_agent = ShellExecutionAgent(
                config={
                    "working_dir": str(working_dir),
                    "allow_execution": policy.allow_execution,
                    "max_output_bytes": policy.max_output_bytes,
                    "command_timeout": policy.timeout_seconds,
                    "security_mode": caller_cfg.get("security_mode", "denylist"),
                    "allowlist": caller_cfg.get("allowlist", []),
                    "denylist": caller_cfg.get("denylist"),
                }
            )
            allowed, reason, parts = shell_agent._validate_command(command)
            if not allowed:
                return {
                    "success": False,
                    "output": None,
                    "error": f"Command rejected: {reason}",
                }

            shim_res = shell_agent._execute_builtin_shim(
                parts, str(working_dir), request.get("task_id", "helper-shell")
            )
            if shim_res is not None:
                return {
                    "success": shim_res.success,
                    "output": shim_res.output,
                    "error": shim_res.error,
                }

            executable = shutil.which(parts[0])
            if executable is None:
                return {
                    "success": False,
                    "output": None,
                    "error": f"Command not found: '{parts[0]}'",
                }

            proc_args = [executable, *parts[1:]]
            proc_out = asyncio.run(
                run_process(
                    proc_args,
                    policy,
                    cwd=working_dir,
                    timeout=timeout,
                )
            )
            stdout_text = proc_out.stdout.decode("utf-8", errors="replace")
            stderr_text = proc_out.stderr.decode("utf-8", errors="replace")
            success = proc_out.returncode == 0
            return {
                "success": success,
                "output": {
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "returncode": proc_out.returncode,
                    "truncated": proc_out.truncated,
                },
                "error": stderr_text if not success else None,
            }
        if operation == "workspace_list_tree":
            import os
            import stat
            import time

            raw_path = request.get("path") or request.get("payload", {}).get("path")
            target_path = policy.path(raw_path or policy.root, write=False)

            payload = request.get("payload", {})
            raw_depth = payload.get("max_depth", request.get("max_depth", 2))
            if type(raw_depth) is not int:
                raise TypeError("max_depth must be an integer")
            if not 0 <= raw_depth <= 20:
                raise ValueError("max_depth must be between 0 and 20")
            max_depth = raw_depth

            raw_entries = payload.get("max_entries", request.get("max_entries", 4096))
            if type(raw_entries) is not int:
                raise TypeError("max_entries must be an integer")
            if not 1 <= raw_entries <= 4096:
                raise ValueError("max_entries must be between 1 and 4096")
            max_entries = raw_entries

            deadline = time.monotonic() + policy.timeout_seconds
            tree: list[str] = []
            acquired_count = 0

            from .policy import PolicyDenied
            from .subagents.workspace import DEFAULT_IGNORES

            def walk_dir(current_dir: Path, depth: int) -> None:
                nonlocal acquired_count
                if depth > max_depth or acquired_count >= max_entries:
                    return
                if time.monotonic() > deadline:
                    raise TimeoutError("workspace_list_tree exceeded timeout")

                raw_items: list[tuple[str, bool, Path]] = []
                try:
                    with os.scandir(current_dir) as it:
                        for entry in it:
                            acquired_count += 1
                            if time.monotonic() > deadline:
                                raise TimeoutError("workspace_list_tree exceeded timeout")

                            entry_name = entry.name
                            if entry_name in DEFAULT_IGNORES or entry_name.endswith(".egg-info"):
                                if acquired_count >= max_entries:
                                    break
                                continue

                            entry_path = current_dir / entry_name

                            # Validate link/reparse policy BEFORE is_dir or following
                            try:
                                lstat_res = entry.stat(follow_symlinks=False)
                            except OSError:
                                if acquired_count >= max_entries:
                                    break
                                continue

                            is_link = entry.is_symlink() or bool(
                                getattr(lstat_res, "st_file_attributes", 0)
                                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                            )
                            if is_link:
                                raise PolicyDenied("Links and reparse paths are unsupported")

                            # Validate path under policy root / readable_roots
                            policy.path(entry_path, write=False)

                            is_dir = entry.is_dir(follow_symlinks=False)
                            raw_items.append((entry_name, is_dir, entry_path))

                            if acquired_count >= max_entries:
                                break
                except PolicyDenied:
                    raise
                except OSError:
                    return

                # Sort only within bounded acquired items
                sorted_items = sorted(raw_items, key=lambda x: (not x[1], x[0].lower()))

                for name, is_dir, p_entry in sorted_items:
                    indent = "  " * (depth - 1)
                    prefix = "📁 " if is_dir else "📄 "
                    tree.append(f"{indent}{prefix}{name}")
                    if is_dir:
                        walk_dir(p_entry, depth + 1)

            if max_depth >= 1:
                walk_dir(target_path, 1)

            return {
                "root": str(target_path),
                "tree": tree,
            }
        raise ValueError("Unsupported internal I/O operation")


def main() -> None:
    data = sys.argv[1].encode() if len(sys.argv) == 2 else sys.stdin.buffer.readline(2_097_153)
    try:
        if len(data) > 2_097_152:
            raise ValueError("I/O helper request exceeds limit")
        request = json.loads(data)
        if not isinstance(request, dict):
            raise TypeError("I/O helper request must be an object")
        result = {"ok": True, "result": execute(request)}
    except (OSError, ValueError, TypeError, RuntimeError, KeyError, sqlite3.Error) as exc:
        result = {"ok": False, "kind": type(exc).__name__, "error": str(exc)[:2048]}
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
