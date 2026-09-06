"""Background task and process management engine.

Provides async subprocess execution for long-running processes (development
servers, test watchers, daemons), output streaming with circular ring buffers,
status tracking, interactive stdin transmission, and graceful process-group teardown.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class BackgroundTask:
    """Represents a background subprocess task."""

    id: str
    command: str
    cwd: str
    status: str = "running"  # "running" | "completed" | "failed" | "killed"
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    returncode: int | None = None
    output_lines: deque[str] = field(default_factory=lambda: deque(maxlen=1000))
    process: asyncio.subprocess.Process | None = None
    _reader_task: asyncio.Task[None] | None = None

    @property
    def uptime_seconds(self) -> float:
        """Calculate task uptime in seconds."""
        if self.end_time is not None:
            return max(0.0, self.end_time - self.start_time)
        return max(0.0, time.time() - self.start_time)

    def to_summary(self) -> dict[str, Any]:
        """Convert task to a lightweight summary dict."""
        last_lines = list(self.output_lines)[-3:] if self.output_lines else []
        return {
            "id": self.id,
            "command": self.command,
            "cwd": self.cwd,
            "status": self.status,
            "uptime_seconds": round(self.uptime_seconds, 2),
            "returncode": self.returncode,
            "total_lines": len(self.output_lines),
            "output_preview": last_lines,
        }

    def to_detail(self) -> dict[str, Any]:
        """Convert task to detailed status dict."""
        return {
            "id": self.id,
            "command": self.command,
            "cwd": self.cwd,
            "status": self.status,
            "start_time": datetime.fromtimestamp(self.start_time, tz=UTC).isoformat(),
            "end_time": (
                datetime.fromtimestamp(self.end_time, tz=UTC).isoformat() if self.end_time else None
            ),
            "uptime_seconds": round(self.uptime_seconds, 2),
            "returncode": self.returncode,
            "total_lines": len(self.output_lines),
            "pid": self.process.pid if self.process else None,
        }


class TaskManager:
    """Manages asynchronous background tasks for the agent session."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self.root_dir = Path(root_dir).resolve() if root_dir else Path.cwd()
        self._tasks: dict[str, BackgroundTask] = {}
        self._counter: int = 0


    def _generate_id(self) -> str:
        self._counter += 1
        return f"task_{self._counter}"

    async def start_task(
        self,
        command: str,
        cwd: str | Path | None = None,
    ) -> BackgroundTask:
        """Start a new command in the background.

        Args:
            command: Shell command string to execute.
            cwd: Working directory (defaults to root_dir).

        Returns:
            The created BackgroundTask instance.
        """
        work_dir = Path(cwd).resolve() if cwd else self.root_dir
        if not work_dir.exists():
            work_dir.mkdir(parents=True, exist_ok=True)

        task_id = self._generate_id()
        task = BackgroundTask(
            id=task_id,
            command=command,
            cwd=str(work_dir),
            status="running",
        )

        creationflags = 0
        if sys.platform == "win32":
            # Start in a new process group for clean subtree termination
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
            task.process = process
        except Exception as exc:  # noqa: BLE001
            task.status = "failed"
            task.end_time = time.time()
            task.returncode = -1
            task.output_lines.append(f"Failed to start task: {exc}")
            self._tasks[task_id] = task
            logger.error("TaskManager: failed to start task %s: %s", task_id, exc)
            return task

        # Start streaming reader coroutine
        task._reader_task = asyncio.create_task(self._read_stream(task))
        self._tasks[task_id] = task
        logger.info(
            "TaskManager: started background task %s (pid %s): %s", task_id, process.pid, command
        )
        return task

    async def _read_stream(self, task: BackgroundTask) -> None:
        """Continuously read process stdout/stderr and capture into buffer."""
        proc = task.process
        if proc is None or proc.stdout is None:
            return

        try:
            while not proc.stdout.at_eof():
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                task.output_lines.append(line)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            task.output_lines.append(f"[Stream reader error: {exc}]")
        finally:
            if proc.returncode is None:
                try:
                    await proc.wait()
                except Exception:  # noqa: BLE001, S110
                    pass
            task.end_time = time.time()
            task.returncode = proc.returncode
            if task.status != "killed":
                task.status = "completed" if proc.returncode == 0 else "failed"

    def list_tasks(self) -> list[dict[str, Any]]:
        """List summaries of all tracked background tasks."""
        return [t.to_summary() for t in self._tasks.values()]

    def get_task(self, task_id: str) -> BackgroundTask | None:
        """Get BackgroundTask by ID."""
        return self._tasks.get(task_id)

    def get_status(self, task_id: str) -> dict[str, Any]:
        """Get detailed status of a task."""
        task = self.get_task(task_id)
        if not task:
            return {"error": f"Task not found: {task_id}", "status": "unknown"}
        return task.to_detail()

    def get_logs(
        self,
        task_id: str,
        tail: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get log lines from a background task."""
        task = self.get_task(task_id)
        if not task:
            return {"error": f"Task not found: {task_id}", "lines": []}

        all_lines = list(task.output_lines)
        total = len(all_lines)

        if offset > 0:
            selected = all_lines[offset : offset + tail]
        else:
            selected = all_lines[-tail:] if tail > 0 else all_lines

        return {
            "task_id": task_id,
            "status": task.status,
            "total_lines": total,
            "returncode": task.returncode,
            "lines": selected,
        }

    async def send_input(self, task_id: str, input_text: str) -> dict[str, Any]:
        """Write input text into stdin of a running task."""
        task = self.get_task(task_id)
        if not task:
            return {"success": False, "error": f"Task not found: {task_id}"}
        if task.status != "running" or task.process is None or task.process.stdin is None:
            return {
                "success": False,
                "error": f"Task {task_id} is not running (status: {task.status})",
            }

        try:
            if not input_text.endswith("\n"):
                input_text += "\n"
            task.process.stdin.write(input_text.encode("utf-8"))
            await task.process.stdin.drain()
            return {"success": True, "task_id": task_id}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"Failed to send stdin to task {task_id}: {exc}"}

    async def kill_task(self, task_id: str) -> dict[str, Any]:
        """Terminate a running background task."""
        task = self.get_task(task_id)
        if not task:
            return {"success": False, "error": f"Task not found: {task_id}"}

        proc = task.process
        if proc is None or task.status != "running":
            return {"success": True, "message": f"Task {task_id} was already {task.status}"}

        task.status = "killed"
        task.end_time = time.time()

        try:
            if sys.platform == "win32" and proc.pid:
                # Force kill process tree on Windows
                subprocess.run(  # noqa: ASYNC221
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except TimeoutError:
                    proc.kill()
        except Exception as exc:  # noqa: BLE001
            logger.warning("TaskManager: error terminating task %s: %s", task_id, exc)

        if task._reader_task and not task._reader_task.done():
            task._reader_task.cancel()

        return {"success": True, "task_id": task_id, "status": "killed"}

    async def cleanup_all(self) -> None:
        """Kill all running background tasks (called on session shutdown)."""
        running_tasks = [t for t in self._tasks.values() if t.status == "running"]
        for task in running_tasks:
            try:
                await self.kill_task(task.id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("TaskManager: cleanup error for task %s: %s", task.id, exc)
