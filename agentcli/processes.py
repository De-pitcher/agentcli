"""Bounded subprocess acquisition and owned process-tree teardown."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .policy import RunPolicy


class ProcessTree:
    """POSIX process group or Windows kill-on-close Job Object."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        from .io_boundary import in_worker

        self.nested = in_worker and sys.platform != "win32"
        self.handle: Any = None
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Basic(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class IO(ctypes.Structure):
                _fields_ = [
                    (n, ctypes.c_uint64)
                    for n in (
                        "ReadOperationCount",
                        "WriteOperationCount",
                        "OtherOperationCount",
                        "ReadTransferCount",
                        "WriteTransferCount",
                        "OtherTransferCount",
                    )
                ]

            class Extended(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", Basic),
                    ("IoInfo", IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            job = kernel.CreateJobObjectW(None, None)
            info = Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            process = kernel.OpenProcess(0x0100 | 0x0001, False, pid)
            try:
                if (
                    not job
                    or not process
                    or not kernel.SetInformationJobObject(
                        job, 9, ctypes.byref(info), ctypes.sizeof(info)
                    )
                    or not kernel.AssignProcessToJobObject(job, process)
                ):
                    if job:
                        kernel.CloseHandle(job)
                    raise OSError("Cannot establish Windows process-tree ownership")
            finally:
                if process:
                    kernel.CloseHandle(process)
            self.handle = job
            self.kernel = kernel

    def close(self) -> None:
        if sys.platform == "win32":
            if self.handle:
                self.kernel.CloseHandle(self.handle)
                self.handle = None
        else:
            try:
                if self.nested:
                    os.kill(self.pid, signal.SIGKILL)
                else:
                    os.killpg(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def spawn_options(policy: RunPolicy) -> dict[str, Any]:
    result: dict[str, Any] = {"env": policy.environment()}
    if sys.platform == "win32":
        result["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        from .io_boundary import in_worker

        # The fixed helper already owns a process group. Its descendants must
        # inherit that group so killing the helper also stops nested commands.
        result["start_new_session"] = not in_worker
    return result


@dataclass
class ProcessOutput:
    returncode: int
    stdout: bytes
    stderr: bytes
    truncated: bool


async def drain(reader: asyncio.StreamReader | None, buffer: bytearray, limit: int) -> bool:
    truncated = False
    if reader is None:
        return False
    while True:
        chunk = await reader.read(min(4096, limit + 1))
        if not chunk:
            return truncated
        remaining = max(0, limit - len(buffer))
        buffer.extend(chunk[:remaining])
        truncated |= len(chunk) > remaining


async def terminate(process: asyncio.subprocess.Process, tree: ProcessTree | None) -> None:
    if tree is not None:
        tree.close()
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await asyncio.wait_for(process.wait(), timeout=5)


async def run_process(
    args: list[str] | str,
    policy: RunPolicy,
    cwd: str | Path | None = None,
    timeout: float | None = None,
    shell: bool = False,
    input_data: bytes | None = None,
) -> ProcessOutput:
    options = dict(
        cwd=str(cwd or policy.root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE if input_data is not None else asyncio.subprocess.DEVNULL,
        **spawn_options(policy),
    )
    if shell:
        if not isinstance(args, str):
            raise TypeError("Shell execution requires a command string")
        process = await asyncio.create_subprocess_shell(args, **options)
    else:
        if not isinstance(args, list):
            raise TypeError("Direct execution requires argument list")
        process = await asyncio.create_subprocess_exec(*args, **options)
    tree = None
    stdout, stderr = bytearray(), bytearray()
    readers = []
    try:
        tree = ProcessTree(process.pid)
        readers = [
            asyncio.create_task(drain(process.stdout, stdout, policy.max_output_bytes)),
            asyncio.create_task(drain(process.stderr, stderr, policy.max_output_bytes)),
        ]
        async with asyncio.timeout(min(timeout or policy.timeout_seconds, policy.timeout_seconds)):
            if input_data is not None and process.stdin is not None:
                process.stdin.write(input_data)
                await process.stdin.drain()
                process.stdin.close()
                await asyncio.shield(process.stdin.wait_closed())
            await process.wait()
            tree.close()  # Any detached descendants remain owned, even after parent exit.
            truncated = any(await asyncio.gather(*readers))
        return ProcessOutput(process.returncode or 0, bytes(stdout), bytes(stderr), truncated)
    finally:
        await terminate(process, tree)
        for reader in readers:
            if not reader.done():
                reader.cancel()
        if readers:
            await asyncio.gather(*readers, return_exceptions=True)


def run_process_sync(
    args: list[str], policy: RunPolicy, cwd: str | None = None, input_data: bytes | None = None
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        args,
        cwd=cwd or str(policy.root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
        **spawn_options(policy),
    )
    tree = None
    buffers = [bytearray(), bytearray()]
    truncated = [False, False]
    threads = []

    def collect(stream: Any, slot: int) -> None:
        try:
            while chunk := stream.read(4096):
                remaining = max(0, policy.max_output_bytes - len(buffers[slot]))
                buffers[slot].extend(chunk[:remaining])
                truncated[slot] |= len(chunk) > remaining
        finally:
            stream.close()

    try:
        tree = ProcessTree(process.pid)
        for slot, stream in enumerate((process.stdout, process.stderr)):
            thread = threading.Thread(target=collect, args=(stream, slot), daemon=True)
            thread.start()
            threads.append(thread)
        if input_data is not None:
            assert process.stdin is not None
            writer_stream = process.stdin

            def send() -> None:
                try:
                    writer_stream.write(input_data)
                    writer_stream.flush()
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    writer_stream.close()

            writer = threading.Thread(target=send, daemon=True)
            writer.start()
            threads.append(writer)
        code = process.wait(timeout=policy.timeout_seconds)
        tree.close()
        for thread in threads:
            thread.join(timeout=5)
        if any(thread.is_alive() for thread in threads):
            raise TimeoutError("Process pipe shutdown timed out")
        if any(truncated):
            raise ValueError("Process output exceeded limit")
        return subprocess.CompletedProcess(
            args, code, buffers[0].decode("utf-8", "replace"), buffers[1].decode("utf-8", "replace")
        )
    finally:
        if tree is not None:
            tree.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)
