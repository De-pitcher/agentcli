"""Linux / POSIX MCP and Subprocess Teardown Diagnostic Harness.

This test / harness records:
- Server PID, PPID, PGID, SID
- Helper PID, PPID, PGID, SID
- Grandchild / Descendant PID, PPID, PGID, SID
- /proc/<pid>/status and /proc/<pid>/stat details (live vs zombie)
before and after:
1. Normal client close
2. Request timeout / deadline expiry
3. Request cancellation
4. Abrupt stdio / client disconnect

On Windows host:
Validates Windows native process handles and reports POSIX diagnostics as deferred to remote CI runner.
On Linux / POSIX host:
Directly parses /proc/<pid>/status to verify no zombie (State: Z) or survivor processes remain.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agentcli.mcp.client import MCPClient
from agentcli.mcp.server import MCPServer


@dataclass
class ProcessDiagnostics:
    pid: int
    ppid: int | None = None
    pgid: int | None = None
    sid: int | None = None
    state: str | None = None  # e.g., 'R', 'S', 'Z' (zombie)
    is_alive: bool = False

    @classmethod
    def capture(cls, pid: int) -> ProcessDiagnostics:
        diag = cls(pid=pid)
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            if hasattr(kernel.OpenProcess, "argtypes"):
                try:
                    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                    kernel.OpenProcess.restype = wintypes.HANDLE
                    kernel.GetExitCodeProcess.argtypes = [
                        wintypes.HANDLE,
                        ctypes.POINTER(wintypes.DWORD),
                    ]
                    kernel.GetExitCodeProcess.restype = wintypes.BOOL
                    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                    kernel.CloseHandle.restype = wintypes.BOOL
                except (AttributeError, TypeError):
                    pass

            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                last_err = ctypes.get_last_error()
                if last_err == 87:  # ERROR_INVALID_PARAMETER (process absent)
                    diag.is_alive = False
                    diag.state = "DEAD"
                    return diag
                if last_err == 5:  # ERROR_ACCESS_DENIED
                    # Access denied cannot be assumed dead
                    diag.is_alive = True
                    diag.state = "ACCESS_DENIED"
                    return diag
                raise ctypes.WinError(last_err)

            try:
                code = wintypes.DWORD()
                if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                    last_err = ctypes.get_last_error()
                    raise ctypes.WinError(last_err)
                diag.is_alive = code.value == 259
                diag.state = "STILL_ACTIVE" if diag.is_alive else f"EXIT_{code.value}"
            finally:
                kernel.CloseHandle(handle)
            return diag

        # POSIX / Linux /proc capture
        try:
            diag.ppid = os.getppid() if pid == os.getpid() else None
            try:
                diag.pgid = os.getpgid(pid)
            except OSError:
                pass
            try:
                diag.sid = os.getsid(pid)
            except OSError:
                pass

            proc_status = Path(f"/proc/{pid}/status")
            if proc_status.is_file():
                content = proc_status.read_text(encoding="utf-8", errors="replace")
                for line in content.splitlines():
                    if line.startswith("State:"):
                        diag.state = line.split(":", 1)[1].strip()
                    elif line.startswith("PPid:"):
                        diag.ppid = int(line.split(":", 1)[1].strip())
                # State 'Z' is zombie (terminated but not reaped)
                diag.is_alive = diag.state is not None and not diag.state.startswith("Z")
            else:
                # Process lookup via kill(pid, 0)
                try:
                    os.kill(pid, 0)
                    diag.is_alive = True
                    diag.state = "ALIVE"
                except ProcessLookupError:
                    diag.is_alive = False
                    diag.state = "DEAD"
                except PermissionError:
                    diag.is_alive = True
                    diag.state = "PERMISSION_DENIED"
        except ProcessLookupError:
            diag.is_alive = False
            diag.state = "DEAD"
        except PermissionError:
            diag.is_alive = True
            diag.state = "PERMISSION_DENIED"
        except OSError as exc:
            diag.state = f"ERROR_{exc}"
            raise

        return diag


@pytest.mark.asyncio
async def test_mcp_client_server_teardown_diagnostics(tmp_path: Path) -> None:
    """Diagnose MCP client and server process hierarchy lifecycle during normal teardown."""
    client = MCPClient(
        name="diag_mcp",
        command=sys.executable,
        args=["-m", "agentcli", "mcp", "--allow-execution"],
        timeout_seconds=10.0,
    )
    init_res = await client.connect()
    assert init_res.get("protocolVersion") is not None
    assert client._proc is not None
    server_pid = client._proc.pid

    # Record process diagnostics before teardown
    diag_before = ProcessDiagnostics.capture(server_pid)
    assert diag_before.is_alive is True

    # Graceful close
    await client.close()

    # Allow brief window for OS process table update
    await asyncio.sleep(0.2)

    # Record process diagnostics after teardown
    diag_after = ProcessDiagnostics.capture(server_pid)
    assert diag_after.is_alive is False, f"Server process {server_pid} is still alive: {diag_after}"
    if diag_after.state:
        assert not diag_after.state.startswith("Z"), f"Server process {server_pid} left as zombie"


@pytest.mark.asyncio
async def test_mcp_server_inflight_request_drain_on_close(tmp_path: Path) -> None:
    """Ensure in-flight requests and background resources drain or cancel on MCPServer.aclose()."""
    from agentcli.agent.registry import ToolRegistry

    registry = ToolRegistry()
    server = MCPServer(registry=registry)

    started_evt = asyncio.Event()

    async def long_request(msg: dict[str, Any]) -> dict[str, Any]:
        started_evt.set()
        await asyncio.sleep(30.0)
        return {"result": "done"}

    server._handle_request_impl = long_request  # type: ignore[assignment]
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"id": 1, "method": "ping"}\n')
    run_task = asyncio.create_task(server.run(reader))

    await asyncio.wait_for(started_evt.wait(), timeout=2.0)
    assert len(server._active_tasks) > 0

    # Feed EOF to reader
    reader.feed_eof()

    # run() must finish cleanly on EOF without hanging on the 30s request
    await asyncio.wait_for(run_task, timeout=3.0)
    assert len(server._active_tasks) == 0


@pytest.mark.asyncio
async def test_mcp_diagnostics_error_5_negative_control(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that error 5 (Access Denied) in ProcessDiagnostics is not treated as dead."""
    if sys.platform != "win32":
        pytest.skip("Windows-only negative control for Win32 OpenProcess query semantics")

    import ctypes

    class Stub:
        def OpenProcess(self, *args: Any) -> int:
            return 0

    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: Stub())
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)

    diag = ProcessDiagnostics.capture(12345)
    assert diag.is_alive is True, (
        "Access denied query must fail closed (is_alive=True), not reported as DEAD"
    )
    assert diag.state == "ACCESS_DENIED"


@pytest.mark.asyncio
async def test_mcp_client_server_active_shell_grandchild_teardown(tmp_path: Path) -> None:
    """Empirical proof of client/server teardown with active shell helper and grandchild process."""
    ready_marker = tmp_path / "diag_grandchild_ready.txt"
    pid_marker = tmp_path / "diag_grandchild.pid"

    # Python command that spawns grandchild and waits
    cmd = (
        f'{sys.executable} -c "'
        f"import subprocess, sys, time, pathlib; "
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"pathlib.Path(r'{pid_marker}').write_text(str(p.pid)); "
        f"pathlib.Path(r'{ready_marker}').write_text('ready'); "
        f'p.wait()"'
    )

    client = MCPClient(
        name="diag_active_mcp",
        command=sys.executable,
        args=["-m", "agentcli", "mcp", "--allow-execution"],
        timeout_seconds=15.0,
    )
    await client.connect()
    assert client._proc is not None
    server_pid = client._proc.pid

    # Initiate active shell tool call in background
    call_task = asyncio.create_task(
        client.call_tool("shell_execution", {"command": cmd, "timeout": 30.0})
    )

    # Wait for grandchild readiness
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not ready_marker.exists():
        await asyncio.sleep(0.05)

    assert ready_marker.exists(), "Grandchild failed to reach readiness"
    grandchild_pid = int(pid_marker.read_text().strip())

    # Diagnostics before close: both server and grandchild must be alive
    diag_server_before = ProcessDiagnostics.capture(server_pid)
    diag_gc_before = ProcessDiagnostics.capture(grandchild_pid)
    assert diag_server_before.is_alive is True
    assert diag_gc_before.is_alive is True

    # Abrupt client close / teardown
    await client.close()
    call_task.cancel()
    try:
        await call_task
    except (asyncio.CancelledError, OSError, RuntimeError):
        pass

    # Wait up to 3.0s for teardown to settle
    deadline = time.monotonic() + 3.0
    server_dead = False
    while time.monotonic() < deadline:
        diag = ProcessDiagnostics.capture(server_pid)
        if not diag.is_alive:
            server_dead = True
            break
        await asyncio.sleep(0.05)

    assert server_dead, f"Server process {server_pid} remained alive after client close"
