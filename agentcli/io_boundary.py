"""Fixed-operation trusted I/O helpers with bounded, owned process lifetimes."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import weakref
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent.checkpoints import FileSnapshot

from .policy import PolicyDenied, RunPolicy
from .processes import run_process, run_process_sync

# Set only by the fixed helper entry point, never by environment or tool payload.
in_worker = False

_slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def policy_data(policy: RunPolicy) -> dict[str, Any]:
    data = asdict(policy)
    data["root"] = str(policy.root)
    data["readable_roots"] = [str(p) for p in policy.readable_roots]
    return data


def filesystem_request(operation: str, policy: RunPolicy, **values: Any) -> dict[str, Any]:
    return {"operation": operation, "policy": policy_data(policy), **values}


def worker_command() -> list[str]:
    return [sys.executable, "-I", "-m", "agentcli._io_worker"]


def _policy(policy: RunPolicy) -> RunPolicy:
    # Internal protocol budget is distinct from the user-facing tool payload cap.
    return replace(policy, max_output_bytes=6 * min(policy.max_file_bytes, 1_048_576) + 4096)


def _input(request: dict[str, Any]) -> bytes:
    data = json.dumps(request, ensure_ascii=False).encode("utf-8")
    if len(data) > 2_097_152:
        raise ValueError("I/O helper request exceeds acquisition limit")
    return data


def _decode(data: str | bytes, code: int, truncated: bool = False) -> Any:
    if code or truncated:
        raise ValueError("I/O helper failed or exceeded its protocol limit")
    response = json.loads(data)
    if not isinstance(response, dict) or "ok" not in response:
        raise ValueError("Invalid I/O helper response")
    if not response["ok"]:
        kind = str(response.get("kind", "OSError"))
        errors = {
            "PermissionError": PermissionError,
            "PolicyDenied": PolicyDenied,
            "FileNotFoundError": FileNotFoundError,
            "ValueError": ValueError,
            "RuntimeError": RuntimeError,
            "KeyError": KeyError,
            "OSError": OSError,
            "TimeoutError": TimeoutError,
            "DataError": sqlite3.DataError,
            "IntegrityError": sqlite3.IntegrityError,
            "OperationalError": sqlite3.OperationalError,
        }
        raise errors.get(kind, OSError)(response.get("error", "I/O helper failed"))
    return response["result"]


async def query(request: dict[str, Any], policy: RunPolicy) -> Any:
    loop = asyncio.get_running_loop()
    slot = _slots.setdefault(loop, asyncio.Semaphore(1))
    # Queue wait and native operation share a deadline. At most one owned I/O
    # helper runs per event loop, independently of provider/task concurrency.
    async with asyncio.timeout(policy.timeout_seconds):
        async with slot:
            output = await run_process(
                worker_command(),
                _policy(policy),
                cwd=Path(sys.executable).parent,
                input_data=_input(request) + b"\n",
            )
            return _decode(output.stdout, output.returncode, output.truncated)


def query_sync(request: dict[str, Any], policy: RunPolicy) -> Any:
    try:
        output = run_process_sync(
            worker_command(),
            _policy(policy),
            cwd=str(Path(sys.executable).parent),
            input_data=_input(request) + b"\n",
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Filesystem/DNS helper exceeded its deadline") from exc
    return _decode(output.stdout, output.returncode)


def pack_snapshot(snapshot: Any) -> dict[str, Any]:
    import base64

    data = asdict(snapshot)
    data["data"] = (
        base64.b64encode(snapshot.data).decode("ascii") if snapshot.data is not None else None
    )
    return data


def unpack_snapshot(data: dict[str, Any]) -> FileSnapshot:
    import base64

    from .agent.checkpoints import FileSnapshot

    data = dict(data)
    if data["data"] is not None:
        data["data"] = base64.b64decode(data["data"], validate=True)
    return FileSnapshot(**data)
