"""Session-owned bounded background processes with explicit terminal states."""

from __future__ import annotations

import asyncio
import os
import shlex
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..policy import RunPolicy
from ..processes import ProcessTree, drain, spawn_options, terminate


@dataclass
class BackgroundTask:
    id: str
    command: str
    cwd: str
    status: str = "running"
    owner_run_id: str | None = None
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    returncode: int | None = None
    process: asyncio.subprocess.Process | None = None
    _reader_task: asyncio.Task[Any] | None = None
    tree: ProcessTree | None = None
    buffer: bytearray = field(default_factory=bytearray)
    truncated: bool = False

    @property
    def output_lines(self) -> deque[str]:
        return deque(self.buffer.decode("utf-8", "replace").splitlines())

    @property
    def uptime_seconds(self) -> float:
        return (self.end_time or time.time()) - self.start_time

    def to_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "status": self.status,
            "returncode": self.returncode,
            "pid": self.process.pid if self.process else None,
            "uptime_seconds": round(self.uptime_seconds, 2),
            "truncated": self.truncated,
        }

    def to_detail(self) -> dict[str, Any]:
        return {
            **self.to_summary(),
            "cwd": self.cwd,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "total_lines": len(self.output_lines),
        }


class TaskManager:
    def __init__(self, root_dir: str | Path | None = None, policy: RunPolicy | None = None) -> None:
        self.policy = policy or RunPolicy(root=Path(root_dir or Path.cwd()))
        self.root_dir = self.policy.root
        self._tasks: dict[str, BackgroundTask] = {}
        self._closed = False

    async def start_task(self, command: str, cwd: str | Path | None = None) -> BackgroundTask:
        work_dir = self.policy.command(command, cwd)
        if self._closed:
            raise RuntimeError("Task manager is closed")
        if sum(t.status == "running" for t in self._tasks.values()) >= self.policy.max_tasks:
            raise ValueError("Active task limit reached")
        while len(self._tasks) >= self.policy.max_retained_tasks:
            completed = next((k for k, t in self._tasks.items() if t.status != "running"), None)
            if completed is None:
                raise ValueError("Task retention limit reached")
            del self._tasks[completed]
        from ..runtime import active_run_id

        task = BackgroundTask(
            "task_" + uuid.uuid4().hex, command, str(work_dir), owner_run_id=active_run_id.get()
        )
        self._tasks[task.id] = task
        try:
            parts = shlex.split(command, posix=os.name != "nt")
            if os.name == "nt":
                parts = [p.strip('"') for p in parts]
            task.process = await asyncio.create_subprocess_exec(
                *parts,
                cwd=str(work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.PIPE,
                **spawn_options(self.policy),
            )
            task.tree = ProcessTree(task.process.pid)
            task._reader_task = asyncio.create_task(self._read_stream(task))
        except asyncio.CancelledError:
            if task.process:
                await terminate(task.process, task.tree)
            task.status = "killed"
            task.end_time = time.time()
            task.returncode = task.process.returncode if task.process else None
            raise
        except Exception as exc:  # noqa: BLE001 — public boundary returns explicit failure
            if task.process:
                await terminate(task.process, task.tree)
            task.status = "failed"
            task.end_time = time.time()
            task.returncode = -1
            task.buffer.extend(str(exc).encode("utf-8")[: self.policy.max_output_bytes])
        return task

    async def _read_stream(self, task: BackgroundTask) -> None:
        proc = task.process
        if proc is None:
            return
        reader = asyncio.create_task(drain(proc.stdout, task.buffer, self.policy.max_output_bytes))
        try:
            async with asyncio.timeout(self.policy.timeout_seconds):
                await proc.wait()
                if task.tree:
                    task.tree.close()
                task.truncated = await reader
        except TimeoutError:
            task.status = "timed_out"
        except asyncio.CancelledError:
            task.status = "killed"
            raise
        finally:
            await terminate(proc, task.tree)
            if proc.stdin:
                proc.stdin.close()
                try:
                    await proc.stdin.wait_closed()
                except (OSError, BrokenPipeError):
                    pass
            if not reader.done():
                reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            task.end_time = time.time()
            task.returncode = proc.returncode
            if task.status == "running":
                task.status = "completed" if proc.returncode == 0 else "failed"

    def list_tasks(self) -> list[dict[str, Any]]:
        return [t.to_summary() for t in self._tasks.values()]

    def get_task(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def get_status(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        return task.to_detail() if task else {"error": "Task not found", "status": "unknown"}

    def get_logs(self, task_id: str, tail: int = 50, offset: int = 0) -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            return {"error": "Task not found", "lines": []}
        lines = list(task.output_lines)
        tail = max(1, min(tail, 1000))
        selected = lines[max(0, offset) : max(0, offset) + tail] if offset > 0 else lines[-tail:]
        return {
            "task_id": task_id,
            "status": task.status,
            "lines": selected,
            "total_lines": len(lines),
            "returncode": task.returncode,
            "truncated": task.truncated,
        }

    async def send_input(self, task_id: str, input_text: str) -> dict[str, Any]:
        self.policy.command("owned stdin")
        task = self.get_task(task_id)
        if not task or not task.process or task.status != "running" or not task.process.stdin:
            return {"success": False, "error": "Task is not running"}
        data = (input_text.rstrip("\n") + "\n").encode("utf-8")
        if len(data) > self.policy.max_output_bytes:
            return {"success": False, "error": "Input exceeds byte limit"}
        try:
            task.process.stdin.write(data)
            await asyncio.wait_for(task.process.stdin.drain(), self.policy.timeout_seconds)
            return {"success": True, "task_id": task_id}
        except (OSError, TimeoutError) as exc:
            return {"success": False, "error": str(exc)}

    async def kill_task(self, task_id: str) -> dict[str, Any]:
        self.policy.command("owned cancellation")
        task = self.get_task(task_id)
        if not task:
            return {"success": False, "error": "Task not found"}
        if task.status == "running" and task._reader_task:
            task._reader_task.cancel()
            await asyncio.gather(task._reader_task, return_exceptions=True)
            task.status = "killed"
        return {"success": True, "task_id": task_id, "status": task.status}

    async def cleanup_all(self) -> None:
        self._closed = True
        for task in self._tasks.values():
            if task._reader_task:
                if not task._reader_task.done():
                    task._reader_task.cancel()
                await asyncio.gather(task._reader_task, return_exceptions=True)
            if task.process is not None:
                await terminate(task.process, task.tree)
                if task.process.stdin:
                    task.process.stdin.close()
                    try:
                        await task.process.stdin.wait_closed()
                    except OSError:
                        pass
                if task.process.stdout:
                    await drain(task.process.stdout, task.buffer, self.policy.max_output_bytes)
                task.returncode = task.process.returncode
                if task.status == "running":
                    task.status = "killed"
                task.end_time = time.time()
