"""Explicit bounded result contracts shared by all dispatch paths."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .policy import RunPolicy

if TYPE_CHECKING:
    from .subagents.base import SubAgentResult


def bound_result(result: SubAgentResult, limit: int) -> SubAgentResult:
    output = result.output
    if output is None:
        return result
    if isinstance(output, dict) and "stdout" in output:
        # Check initial serialized length of the complete output dictionary
        ser_data = json.dumps(output, ensure_ascii=False).encode("utf-8")
        if len(ser_data) > limit:
            output["truncated"] = True
            stdout_str = str(output.get("stdout", ""))
            stderr_str = str(output.get("stderr", ""))

            # Calculate base envelope overhead without stream contents, retaining all metadata fields (cwd, returncode, truncated, etc.)
            base_probe = {k: v for k, v in output.items() if k not in ("stdout", "stderr")}
            base_probe["stdout"] = ""
            base_probe["stderr"] = ""
            overhead = len(json.dumps(base_probe, ensure_ascii=False).encode("utf-8"))
            if overhead > limit:
                # If non-stream metadata alone cannot fit, fail closed cleanly without leaving oversized output
                result.success = False
                result.error = "Output limit cannot represent required envelope"
                result.output = None
                return result

            budget = limit - overhead
            out_bytes = stdout_str.encode("utf-8")
            err_bytes = stderr_str.encode("utf-8")
            tot = len(out_bytes) + len(err_bytes)

            if tot == 0:
                output["stdout"] = ""
                output["stderr"] = ""
            else:
                out_alloc = max(0, int(budget * len(out_bytes) / tot))
                err_alloc = max(0, budget - out_alloc)
                cur_out = out_bytes[:out_alloc].decode("utf-8", "ignore")
                cur_err = err_bytes[:err_alloc].decode("utf-8", "ignore")

                def fits(s_out: str, s_err: str) -> bool:
                    t = dict(base_probe)
                    t["stdout"] = s_out
                    t["stderr"] = s_err
                    return len(json.dumps(t, ensure_ascii=False).encode("utf-8")) <= limit

                while not fits(cur_out, cur_err) and (cur_out or cur_err):
                    excess = (
                        len(
                            json.dumps(
                                {**base_probe, "stdout": cur_out, "stderr": cur_err},
                                ensure_ascii=False,
                            ).encode("utf-8")
                        )
                        - limit
                    )
                    drop = max(1, excess // 2)
                    if len(cur_out) >= len(cur_err) and cur_out:
                        cur_out = cur_out[:-drop] if len(cur_out) > drop else ""
                    elif cur_err:
                        cur_err = cur_err[:-drop] if len(cur_err) > drop else ""

                output["stdout"] = cur_out
                output["stderr"] = cur_err
    elif len(json.dumps(output, default=str).encode("utf-8")) > limit:
        controls = {}
        if isinstance(output, dict):
            controls = {
                key: output[key]
                for key in (
                    "id",
                    "task_id",
                    "status",
                    "returncode",
                    "return_code",
                    "exit_code",
                    "executed",
                )
                if key in output
            }
        if isinstance(output, dict) and isinstance(output.get("tasks"), list):
            controls["tasks"] = [
                {
                    key: row[key]
                    for key in ("id", "task_id", "status", "returncode", "return_code", "exit_code")
                    if key in row
                }
                for row in output["tasks"]
                if isinstance(row, dict)
            ]
            if "total" in output:
                controls["total"] = output["total"]
        envelope = {**controls, "content": "", "truncated": True, "evidence_available": False}
        overhead = len(json.dumps(envelope, default=str).encode("utf-8"))
        # If even the minimal envelope exceeds the limit, reject the result.
        # This applies regardless of whether controls are present.
        if overhead > limit:
            result.success = False
            result.error = "Output limit cannot represent required envelope"
            result.output = None
            return result
        # Reserve JSON string escape expansion as well as the required envelope.
        budget = max(0, (limit - overhead) // 6)
        envelope["content"] = (
            json.dumps(output, default=str).encode("utf-8")[:budget].decode("utf-8", "ignore")
        )
        result.output = envelope
    return result


def read_text(path: Any, policy: Any, encoding: str = "utf-8") -> str:
    from .io_boundary import filesystem_request, in_worker, query_sync

    if not in_worker:
        return str(
            query_sync(
                filesystem_request("read_text", policy, path=str(path), encoding=encoding), policy
            )
        )
    target = policy.path(path)
    with target.open("rb") as stream:
        data: bytes = stream.read(policy.max_file_bytes + 1)
    if len(data) > policy.max_file_bytes:
        raise ValueError("File exceeds acquisition limit")
    return data.decode(encoding, errors="replace")


def walk_entries(
    root: Path,
    policy: RunPolicy,
    excludes: set[str] | frozenset[str] = frozenset(),
    *,
    max_entries: int = 4096,
    max_depth: int = 20,
) -> Iterator[tuple[Path, int, bool]]:
    """Acquire at most max_entries directory entries; never follow links/junctions.

    Native traversal occurs inside an owned helper; its caller enforces a
    process deadline. Checks within the helper are cooperative between calls.
    Synchronous callers wait for the deadline; this is not an OS I/O sandbox.
    """
    from .io_boundary import filesystem_request, in_worker, query_sync

    if not in_worker:
        rows = query_sync(
            filesystem_request(
                "walk",
                policy,
                path=str(root),
                excludes=sorted(excludes),
                max_entries=max_entries,
                max_depth=max_depth,
            ),
            policy,
        )
        for path, depth, directory in rows["entries"]:
            yield Path(path), depth, directory
        if rows["error"]:
            raise ValueError(rows["error"])
        return
    root = policy.path(root)
    if not 0 <= max_depth <= 20:
        raise ValueError("Traversal depth must be between zero and 20")
    deadline = time.monotonic() + policy.timeout_seconds
    seen = 0
    pending = [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        if time.monotonic() > deadline:
            raise TimeoutError("Filesystem traversal deadline exceeded")
        if directory.is_file():
            yield directory, depth, False
            continue
        if depth >= max_depth:
            continue
        with os.scandir(directory) as entries:
            for entry in entries:
                seen += 1
                if seen > max_entries:
                    raise ValueError("Filesystem traversal entry limit exceeded; narrow scope")
                if time.monotonic() > deadline:
                    raise TimeoutError("Filesystem traversal deadline exceeded")
                if entry.name in excludes or entry.name.endswith(".egg-info"):
                    continue
                try:
                    path = policy.path(Path(entry.path))
                except PermissionError:
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                yield path, depth + 1, is_dir
                if is_dir:
                    pending.append((path, depth + 1))


def scan_mtimes(
    paths: list[Path], policy: RunPolicy, ignored: set[str], extensions: set[str]
) -> dict[Path, float]:
    """Acquire a bounded watcher snapshot only inside the owned I/O helper."""
    from .io_boundary import in_worker

    if not in_worker:
        raise RuntimeError("Native watcher scans require the owned helper")
    if len(paths) > 32:
        raise ValueError("Watcher root count exceeds limit")
    current: dict[Path, float] = {}
    for value in paths:
        root = policy.path(value)
        if not root.exists():
            continue
        single_file = root.is_file()
        entries = [(root, 0, False)] if single_file else walk_entries(root, policy, ignored)
        for path, _, directory in entries:
            if directory or (
                not single_file
                and (
                    path.name in ignored
                    or any(path.name.startswith(name) for name in ignored if name.startswith("."))
                )
            ):
                continue
            if extensions and path.suffix not in extensions:
                continue
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if path not in current and len(current) >= 4096:
                raise ValueError("Watcher snapshot count exceeds limit")
            current[path] = modified
    return current
