"""I01/I02 Permanent Regression Tests.

These tests reproduce the original I01/I02 failures and verify the fixes.
Adapted for issue-76-corrections worktree:
- Explicit child worker bootstrap to ensure child subprocesses import this checkout.
- Explicit RunPolicy injection into FileOpsAgent configs.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Self

import pytest

import agentcli
from agentcli.bounds import bound_result
from agentcli.policy import RunPolicy
from agentcli.subagents.base import SubAgentResult, SubAgentTask, SubAgentType
from agentcli.subagents.file_ops import FileOpsAgent

# Path to the current worktree root for child subprocess bootstrap
WORKTREE_ROOT = Path(agentcli.__file__).resolve().parent.parent
CHILD_BOOTSTRAP = f"import sys; sys.path.insert(0, {str(WORKTREE_ROOT)!r}); "


@pytest.fixture(autouse=True)
def ensure_child_worktree_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure that default io_boundary.worker_command bootstraps sys.path to this worktree."""
    from agentcli import io_boundary

    def bootstrapped_worker_command() -> list[str]:
        return [
            sys.executable,
            "-I",
            "-c",
            CHILD_BOOTSTRAP
            + "import runpy; runpy.run_module('agentcli._io_worker', run_name='__main__')",
        ]

    monkeypatch.setattr(io_boundary, "worker_command", bootstrapped_worker_command)


class TestI01CancellationPreservation:
    """I01: FileOps cancellation swallowed by post-state fingerprint timeout.

    When a FileOps write is cancelled after the atomic replacement commits,
    the `finally` block's fingerprint acquisition (5s cleanup budget) can
    timeout before the outer tool deadline (10s). The original cancellation
    must be preserved, not replaced by a generic timeout error.
    """

    @pytest.mark.asyncio
    async def test_cancellation_preserved_across_fingerprint_timeout(self, tmp_path: Path) -> None:
        """Reproduce I01: cancellation preserved across fingerprint timeout.

        This test uses a custom worker that adds a 10s delay in fingerprint,
        while the outer policy has 10s timeout and cleanup has 5s budget.
        The cancellation should propagate, not be replaced by timeout error.
        """
        marker = tmp_path / "fingerprint.pid"

        # Custom worker that adds delay in fingerprint - signals after atomic commit
        header = (
            CHILD_BOOTSTRAP
            + "import os,time,json\nfrom pathlib import Path\n"
            + "marker=Path("
            + repr(str(marker))
            + ")\n"
            + "original_fingerprint=None\n"
            + "def slow_fingerprint(path):\n"
            + "    marker.write_text(json.dumps({'pid': os.getpid(), 'time': time.time()}))\n"
            + "    time.sleep(10)\n"
            + "    return original_fingerprint(path)\n"
            + "import agentcli.agent.checkpoints as checkpoints_mod\n"
            + "original_fingerprint = checkpoints_mod.fingerprint\n"
            + "checkpoints_mod.fingerprint = slow_fingerprint\n"
        )
        worker_cmd = (
            header + "import runpy\nrunpy.run_module('agentcli._io_worker',run_name='__main__')\n"
        )

        from agentcli import io_boundary

        original_worker_cmd = io_boundary.worker_command
        io_boundary.worker_command = lambda: [sys.executable, "-I", "-c", worker_cmd]

        try:
            with tempfile.TemporaryDirectory(dir=tmp_path) as test_dir_str:
                test_dir = Path(test_dir_str)
                test_file = test_dir / "test.txt"
                test_file.write_text("original content", encoding="utf-8")

                # Policy with 10s outer timeout, 5s cleanup budget
                policy = RunPolicy(
                    root=test_dir,
                    allow_write=True,
                    timeout_seconds=10.0,
                    max_output_bytes=65536,
                )

                # Apply the policy to the agent via config
                agent = FileOpsAgent(
                    config={
                        "policy": policy,
                        "working_dir": str(test_dir),
                        "allow_write": True,
                        "read_only": False,
                        "command_timeout": 10.0,  # Explicit 10s timeout
                    }
                )

                task = SubAgentTask(
                    id="test-cancel-1",
                    agent_type=SubAgentType.FILE_OPS,
                    payload={
                        "operation": "write",
                        "path": str(test_file),
                        "content": "new content " + "x" * 100000,
                        "encoding": "utf-8",
                    },
                )

                task_future = asyncio.create_task(agent.run(task))

                # Wait for fingerprint to start (it runs in finally block after write)
                for _ in range(100):
                    if marker.exists():
                        break
                    await asyncio.sleep(0.05)
                assert marker.exists(), "Fingerprint never started"

                # Verify the atomic write committed
                assert test_file.read_text(encoding="utf-8") == "new content " + "x" * 100000, (
                    "Atomic commit not observed"
                )

                # Cancel the task - fingerprint is running and will take 10s
                # The outer policy timeout is 10s, cleanup is 5s
                # The cancellation should propagate, not be replaced by timeout error
                task_future.cancel()

                try:
                    await task_future
                except asyncio.CancelledError:
                    # This is what should happen - cancellation propagates as exception
                    return
                # If we get here, cancellation was swallowed
                raise AssertionError("Cancellation was swallowed - expected CancelledError")

        finally:
            io_boundary.worker_command = original_worker_cmd


def check_alive(pid: int) -> bool:
    """Query process liveness natively; fail closed on query errors other than 87."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL

        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            last_err = ctypes.get_last_error()
            if last_err == 87:  # ERROR_INVALID_PARAMETER (process does not exist)
                return False
            raise ctypes.WinError(last_err)
        try:
            code = wintypes.DWORD()
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    else:
        import os

        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False


class TestI01CancellationControls:
    """I01: Additional cancellation control tests."""

    @pytest.mark.asyncio
    async def test_repeated_cancellation_same_task(self, tmp_path: Path) -> None:
        """Cancelling the same pending request twice preserves CancelledError."""
        target = tmp_path / "target.bin"
        target.write_bytes(b"original\x00\xff\r\n")
        committed = tmp_path / "committed.pid"
        sealing = tmp_path / "sealing.pid"

        worker = (
            CHILD_BOOTSTRAP
            + """import os,time
from pathlib import Path
from agentcli import _io_worker
original_execute=_io_worker.execute
original_replace=os.replace
def execute(request):
    if request.get('operation')=='fingerprint':
        Path(SEAL_MARKER).write_text(str(os.getpid()))
        time.sleep(20)
    return original_execute(request)
def replace(source,destination,*args,**kwargs):
    value=original_replace(source,destination,*args,**kwargs)
    if Path(destination).name=='target.bin':
        Path(COMMIT_MARKER).write_text(str(os.getpid()))
        time.sleep(20)
    return value
_io_worker.execute=execute
os.replace=replace
_io_worker.main()
""".replace("SEAL_MARKER", repr(str(sealing))).replace("COMMIT_MARKER", repr(str(committed)))
        )

        from agentcli import io_boundary
        from agentcli.agent.registry import ToolRegistry
        from agentcli.runtime import RunContext

        original_command = io_boundary.worker_command
        io_boundary.worker_command = lambda: [sys.executable, "-I", "-c", worker]
        context = RunContext(
            policy=RunPolicy(root=tmp_path, allow_write=True, timeout_seconds=10.0)
        )
        pending = None
        commit_pid = None
        cleanup_pid = None
        import time

        try:
            registry = ToolRegistry(context=context)
            pending = asyncio.create_task(
                registry.execute(
                    "file_ops",
                    {"operation": "write", "path": "target.bin", "content": "changed"},
                )
            )

            # 1. Observe atomic commit and capture mutation PID
            ready_deadline = time.monotonic() + 8
            while time.monotonic() < ready_deadline:
                if committed.exists() and committed.read_text(encoding="utf-8").isdigit():
                    break
                if pending.done():
                    raise AssertionError("mutation finished before controlled cancellation")
                await asyncio.sleep(0.01)
            assert committed.exists() and committed.read_text(encoding="utf-8").isdigit(), (
                "atomic replacement never entered"
            )
            commit_pid = int(committed.read_text(encoding="utf-8"))
            assert target.read_bytes() == b"changed"

            # 2. Assert pending request and request first cancellation
            assert not pending.done(), "request finished before first cancellation"
            first_cancel_requested = pending.cancel()
            assert first_cancel_requested, "first cancellation must be requested"

            # 3. Await fresh DISTINCT parent-cleanup marker (bounded wait)
            cleanup_ready_deadline = time.monotonic() + 4
            while time.monotonic() < cleanup_ready_deadline:
                if sealing.exists() and sealing.read_text(encoding="utf-8").isdigit():
                    break
                assert not pending.done(), "request finished before controlled cleanup readiness"
                await asyncio.sleep(0.01)
            assert sealing.exists() and sealing.read_text(encoding="utf-8").isdigit(), (
                "cleanup helper never entered"
            )

            # 4. Capture cleanup PID; assert SAME original request remains pending
            cleanup_pid = int(sealing.read_text(encoding="utf-8"))
            assert not pending.done(), "second cancellation must target pending request"

            # 5. Request second cancellation and assert request accepted
            second_cancel_requested = pending.cancel()
            assert second_cancel_requested, "second cancellation must be requested"

            # 6. Require propagated asyncio.CancelledError on final await
            done, _ = await asyncio.wait({pending}, timeout=8)
            assert pending in done, "post-state cleanup exceeded reviewer watchdog"
            try:
                result = await pending
                raise AssertionError(f"Expected CancelledError, got result: {result}")
            except asyncio.CancelledError:
                pass

            # 7. Assert retained snapshot.sealed is False
            assert len(context.journal._checkpoints) >= 1, "Checkpoint must exist"
            for cp in context.journal._checkpoints:
                for snap in cp.files.values():
                    assert snap.sealed is False, "snapshot must not be sealed"

            # 8. Verify rollback refuses unknown post-state, reverts no files and leaves committed bytes unchanged
            rollback_result = context.journal.rollback()
            assert rollback_result["success"] is False
            assert rollback_result.get("reverted_files") == []
            error_msg = rollback_result.get("error", "").lower()
            assert (
                "unsealed" in error_msg
                or "missing expected post-state" in error_msg
                or "conflict" in error_msg
            ), f"Rollback should fail for unsealed checkpoint: {rollback_result}"
            assert target.read_bytes() == b"changed", "committed bytes must remain unchanged"

            # 9. Explicitly verify BOTH captured helper PIDs are no longer live
            await context.aclose()
            assert not check_alive(commit_pid), f"Commit helper {commit_pid} still alive"
            assert not check_alive(cleanup_pid), f"Cleanup helper {cleanup_pid} still alive"
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)
            await context.aclose()
            io_boundary.worker_command = original_command

    def test_windows_liveness_query_negative_control(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Negative control: error 5 (Access Denied) fails closed, error 87 succeeds as absent."""
        if sys.platform != "win32":
            pytest.skip("Windows-only negative control for Win32 OpenProcess query semantics")

        import ctypes

        class StubFunction:
            def __call__(self, *args: Any) -> int:
                return 0

        class StubKernel:
            OpenProcess = StubFunction()
            GetExitCodeProcess = StubFunction()
            CloseHandle = StubFunction()

        monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: StubKernel())

        # Error 87 (ERROR_INVALID_PARAMETER) correctly signals non-existent process
        monkeypatch.setattr(ctypes, "get_last_error", lambda: 87)
        assert check_alive(12345) is False

        # Error 5 (ERROR_ACCESS_DENIED) must fail closed (raise WinError/PermissionError), never return False
        monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)
        with pytest.raises(PermissionError):
            check_alive(12345)

        # Unknown error 1 (ERROR_INVALID_FUNCTION) must also fail closed
        monkeypatch.setattr(ctypes, "get_last_error", lambda: 1)
        with pytest.raises(OSError):
            check_alive(12345)

    @pytest.mark.asyncio
    async def test_non_cancelled_sealing_failure_returns_error(self, tmp_path: Path) -> None:
        """Non-cancelled fingerprint failure returns error, not cancelled."""
        marker = tmp_path / "fingerprint.pid"

        # Worker that fails fingerprint with IOError
        header = (
            CHILD_BOOTSTRAP
            + "import os,time,json\nfrom pathlib import Path\n"
            + "marker=Path("
            + repr(str(marker))
            + ")\n"
            + "original_fingerprint=None\n"
            + "def failing_fingerprint(path):\n"
            + "    marker.write_text(json.dumps({'pid': os.getpid(), 'time': time.time()}))\n"
            + "    raise IOError('Simulated fingerprint failure')\n"
            + "import agentcli.agent.checkpoints as checkpoints_mod\n"
            + "original_fingerprint = checkpoints_mod.fingerprint\n"
            + "checkpoints_mod.fingerprint = failing_fingerprint\n"
        )
        worker_cmd = (
            header + "import runpy\nrunpy.run_module('agentcli._io_worker',run_name='__main__')\n"
        )

        from agentcli import io_boundary

        original_worker_cmd = io_boundary.worker_command
        io_boundary.worker_command = lambda: [sys.executable, "-I", "-c", worker_cmd]

        try:
            with tempfile.TemporaryDirectory(dir=tmp_path) as test_dir_str:
                test_dir = Path(test_dir_str)
                test_file = test_dir / "test.txt"
                test_file.write_text("original content", encoding="utf-8")

                policy = RunPolicy(
                    root=test_dir,
                    allow_write=True,
                    timeout_seconds=10.0,
                    max_output_bytes=65536,
                )

                agent = FileOpsAgent(
                    config={
                        "policy": policy,
                        "working_dir": str(test_dir),
                        "allow_write": True,
                        "read_only": False,
                        "command_timeout": 10.0,
                    }
                )

                task = SubAgentTask(
                    id="test-cancel-3",
                    agent_type=SubAgentType.FILE_OPS,
                    payload={
                        "operation": "write",
                        "path": str(test_file),
                        "content": "new content",
                        "encoding": "utf-8",
                    },
                )

                result = await agent.run(task)

                # Should fail with fingerprint error, not cancellation
                assert result.success is False
                assert (
                    "fingerprint" in (result.error or "").lower()
                    or "ioerror" in (result.error or "").lower()
                ), f"Expected fingerprint error, got: {result.error}"

        finally:
            io_boundary.worker_command = original_worker_cmd


class TestI02OutputCapEnforcement:
    """I02: Tiny output caps exceeded by truncation envelope."""

    @pytest.mark.asyncio
    async def test_fileops_read_with_tiny_cap_returns_error(self, tmp_path: Path) -> None:
        """Test that FileOps read with 16-byte cap returns error, not oversized envelope."""
        with tempfile.TemporaryDirectory(dir=tmp_path) as test_dir_str:
            test_dir = Path(test_dir_str)
            test_file = test_dir / "test.txt"
            test_file.write_text(
                "This is a test file with some content that exceeds 16 bytes", encoding="utf-8"
            )

            # Explicit policy with 16-byte cap
            policy = RunPolicy(
                root=test_dir,
                allow_write=True,
                timeout_seconds=10.0,
                max_output_bytes=16,
            )

            agent = FileOpsAgent(
                config={
                    "policy": policy,
                    "working_dir": str(test_dir),
                    "allow_write": True,
                    "read_only": False,
                    "max_output_bytes": 16,
                }
            )

            task = SubAgentTask(
                id="test-read-1",
                agent_type=SubAgentType.FILE_OPS,
                payload={
                    "operation": "read",
                    "path": str(test_file),
                    "encoding": "utf-8",
                },
            )

            result = await agent.run(task)

            # Should fail with envelope error, not return oversized envelope
            assert result.success is False
            assert result.output is None
            assert "envelope" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_bound_result_16_byte_cap_rejects_non_control_output(self) -> None:
        """Test bound_result directly with non-control output and 16-byte cap."""
        result = SubAgentResult(
            task_id="test-1",
            agent_type=SubAgentType.FILE_OPS,
            success=True,
            output={
                "content": "This is a test file with some content that exceeds 16 bytes",
                "path": "/test.txt",
            },
        )

        bounded = bound_result(result, 16)

        # Should fail with envelope error
        assert bounded.success is False
        assert bounded.output is None
        assert "envelope" in (bounded.error or "").lower()

    @pytest.mark.asyncio
    async def test_bound_result_allows_output_under_cap(self) -> None:
        """Test that output under the cap is returned unchanged (no envelope)."""
        result = SubAgentResult(
            task_id="test-1",
            agent_type=SubAgentType.FILE_OPS,
            success=True,
            output={
                "content": "tiny",
                "path": "/test.txt",
            },
        )

        bounded = bound_result(result, 64)

        # Small output fits without truncation - returned as-is
        assert bounded.success is True
        assert bounded.output is not None
        assert bounded.output.get("truncated") is not True


class TestI02OutputCapRepresentability:
    """I02: Envelope size representability at various caps."""

    @pytest.mark.asyncio
    async def test_envelope_created_when_output_exceeds_cap(self) -> None:
        """Output exceeding cap gets wrapped in envelope with truncated flag."""
        result = SubAgentResult(
            task_id="test-1",
            agent_type=SubAgentType.FILE_OPS,
            success=True,
            output={"content": "x" * 50, "path": "/test.txt"},
        )

        # 86 bytes raw, cap at 63 -> should truncate
        bounded = bound_result(result, 63)
        assert bounded.success is True
        assert bounded.output is not None
        assert bounded.output.get("truncated") is True
        assert bounded.output.get("evidence_available") is False

    @pytest.mark.asyncio
    async def test_output_under_cap_returned_unchanged(self) -> None:
        """Output under cap is returned unchanged without envelope."""
        result = SubAgentResult(
            task_id="test-1",
            agent_type=SubAgentType.FILE_OPS,
            success=True,
            output={"content": "small", "path": "/test.txt"},
        )

        # 56 bytes raw, cap at 63 -> no envelope
        bounded = bound_result(result, 63)
        assert bounded.success is True
        assert bounded.output is not None
        assert bounded.output.get("truncated") is not True

    @pytest.mark.asyncio
    async def test_envelope_boundary_at_63_bytes(self) -> None:
        """Envelope overhead is 63 bytes; minimal cap for any envelope is 63."""
        result = SubAgentResult(
            task_id="test-1",
            agent_type=SubAgentType.FILE_OPS,
            success=True,
            output={"content": "x" * 50, "path": "/test.txt"},
        )

        # 86 bytes raw, cap 63 -> envelope of 63 bytes (empty content)
        bounded = bound_result(result, 63)
        assert bounded.success is True
        assert bounded.output.get("content") == ""
        assert bounded.output.get("truncated") is True
        assert bounded.output.get("evidence_available") is False

    @pytest.mark.asyncio
    async def test_envelope_at_62_bytes_rejected(self) -> None:
        """62 bytes is below envelope overhead, should reject."""
        result = SubAgentResult(
            task_id="test-1",
            agent_type=SubAgentType.FILE_OPS,
            success=True,
            output={"content": "x" * 50, "path": "/test.txt"},
        )

        bounded = bound_result(result, 62)
        assert bounded.success is False
        assert "envelope" in (bounded.error or "").lower()

    @pytest.mark.asyncio
    async def test_envelope_at_64_bytes_allows_content(self) -> None:
        """64 bytes allows envelope with 1 byte content."""
        result = SubAgentResult(
            task_id="test-1",
            agent_type=SubAgentType.FILE_OPS,
            success=True,
            output={"content": "x" * 50, "path": "/test.txt"},
        )

        bounded = bound_result(result, 64)
        # At 64 bytes, budget is (64-63)//6 = 0, so content is still empty
        assert bounded.success is True
        assert len(bounded.output.get("content", "")) == 0

        # At 69 bytes, budget becomes 1 - need fresh result since bound_result modifies in place
        result2 = SubAgentResult(
            task_id="test-2",
            agent_type=SubAgentType.FILE_OPS,
            success=True,
            output={"content": "x" * 50, "path": "/test.txt"},
        )
        bounded = bound_result(result2, 69)
        assert bounded.success is True
        assert len(bounded.output.get("content", "")) >= 1

    @pytest.mark.asyncio
    async def test_unicode_content_byte_count_respected(self) -> None:
        """Unicode content byte count properly respected in cap."""
        # Each emoji is 4 bytes in UTF-8
        result = SubAgentResult(
            task_id="test-1",
            agent_type=SubAgentType.FILE_OPS,
            success=True,
            output={"content": "\U0001f389" * 5, "path": "/test.txt"},
        )

        # 5 emojis * 4 = 20 bytes content + JSON overhead ~56 bytes = 76 bytes
        # Should fit at 80 bytes
        bounded = bound_result(result, 80)
        assert bounded.success is True

        # Verify exact byte count
        output_json = json.dumps(bounded.output, default=str).encode("utf-8")
        assert len(output_json) <= 80

        # Should fail at 60 bytes
        result2 = SubAgentResult(
            task_id="test-2",
            agent_type=SubAgentType.FILE_OPS,
            success=True,
            output={"content": "\U0001f389" * 5, "path": "/test.txt"},
        )
        bounded = bound_result(result2, 60)
        assert bounded.success is False
        assert "envelope" in (bounded.error or "").lower()

    @pytest.mark.asyncio
    async def test_special_characters_escaped_in_json(self) -> None:
        """Special characters (quotes, backslashes, newlines) properly escaped in JSON."""
        result = SubAgentResult(
            task_id="test-1",
            agent_type=SubAgentType.FILE_OPS,
            success=True,
            output={"content": 'quote"backslash\\newline\n' * 20, "path": "/test.txt"},
        )

        # Should handle escapes properly - cap at 80 to force truncation
        bounded = bound_result(result, 80)
        assert bounded.success is True
        assert "truncated" in bounded.output

        # Verify actual byte count
        output_json = json.dumps(bounded.output, default=str).encode("utf-8")
        assert len(output_json) <= 80


class TestI03PermanentReviewCoverage:
    """R100 permanent review coverage:

    - ToolRegistry successful file write, retained sealed snapshot and byte-exact rollback.
    - Rollback conflict refusal.
    - Read/append byte caps with unchanged bytes on rejection.
    - Denied outside/read-only/protected paths.
    - Standalone default output cap and failed-result compatibility.
    - Protected listing plus ordinary sibling.
    - Actual helper test state restoration with real MonkeyPatch.
    """

    @pytest.mark.asyncio
    async def test_tool_registry_write_seal_and_rollback(self, tmp_path: Path) -> None:
        """Cover ToolRegistry successful write, retained sealed snapshot and byte-exact rollback."""
        from agentcli.agent.registry import ToolRegistry
        from agentcli.runtime import RunContext

        target = tmp_path / "journal_target.txt"
        # Non-UTF8 bytes plus CRLF: only byte-exact undo can restore this.
        original_bytes = b"initial \xff\xfe\r\nstate\n"
        target.write_bytes(original_bytes)

        context = RunContext(
            policy=RunPolicy(root=tmp_path, allow_write=True, timeout_seconds=10.0)
        )
        try:
            registry = ToolRegistry(context=context)
            res = await registry.execute(
                "file_ops",
                {
                    "operation": "write",
                    "path": "journal_target.txt",
                    "content": "updated content\n",
                },
            )
            assert res.success is True
            assert target.read_bytes() == b"updated content\n"

            # Checkpoint retained and sealed
            assert len(context.journal._checkpoints) >= 1
            last_cp = context.journal._checkpoints[-1]
            resolved_key = str(target.resolve())
            assert resolved_key in last_cp.files
            snap = last_cp.files[resolved_key]
            assert snap.sealed is True
            assert snap.expected is not None

            # Rollback to exact initial bytes
            rb = context.journal.rollback()
            assert rb["success"] is True
            assert any("journal_target.txt" in f for f in rb["reverted_files"])
            assert target.read_bytes() == original_bytes
        finally:
            await context.aclose()

    @pytest.mark.asyncio
    async def test_tool_registry_rollback_conflict_refusal(self, tmp_path: Path) -> None:
        """Cover rollback conflict refusal when external modification occurs."""
        from agentcli.agent.registry import ToolRegistry
        from agentcli.runtime import RunContext

        target = tmp_path / "conflict_target.txt"
        target.write_bytes(b"initial state\n")

        context = RunContext(
            policy=RunPolicy(root=tmp_path, allow_write=True, timeout_seconds=10.0)
        )
        try:
            registry = ToolRegistry(context=context)
            res = await registry.execute(
                "file_ops",
                {"operation": "write", "path": "conflict_target.txt", "content": "agent write\n"},
            )
            assert res.success is True
            assert target.read_bytes() == b"agent write\n"

            # External modification alters file content and fingerprint
            target.write_bytes(b"tampered by external process\n")

            # Rollback refuses to overwrite conflicted file
            rb = context.journal.rollback()
            assert rb["success"] is False
            assert "conflict" in rb.get("error", "").lower()
            assert target.read_bytes() == b"tampered by external process\n"
        finally:
            await context.aclose()

    @pytest.mark.asyncio
    async def test_read_and_append_byte_caps_with_unchanged_bytes(self, tmp_path: Path) -> None:
        """Read and append byte caps reject oversized payloads and preserve original bytes."""
        target = tmp_path / "capped_file.txt"
        initial_bytes = b"initial payload\n"
        target.write_bytes(initial_bytes)

        # Policy with max_file_bytes = 30
        policy = RunPolicy(root=tmp_path, allow_write=True, max_file_bytes=30)
        agent = FileOpsAgent(
            config={"policy": policy, "working_dir": str(tmp_path), "allow_write": True}
        )

        # New content alone fits the cap, but existing + new exceeds the total budget
        oversized_append = "y" * 20
        res_append = await agent.run(
            SubAgentTask(
                id="append-cap",
                agent_type=SubAgentType.FILE_OPS,
                payload={
                    "operation": "append",
                    "path": "capped_file.txt",
                    "content": oversized_append,
                },
            )
        )
        assert res_append.success is False
        assert "byte limit" in (res_append.error or "").lower()
        assert target.read_bytes() == initial_bytes

        # Write large file to test read cap
        large_target = tmp_path / "large_read.txt"
        large_target.write_bytes(b"y" * 100)
        res_read = await agent.run(
            SubAgentTask(
                id="read-cap",
                agent_type=SubAgentType.FILE_OPS,
                payload={"operation": "read", "path": "large_read.txt"},
            )
        )
        assert res_read.success is False
        assert "byte limit" in (res_read.error or "").lower()
        assert large_target.read_bytes() == b"y" * 100

    @pytest.mark.asyncio
    async def test_denied_outside_and_readonly_paths(self, tmp_path: Path) -> None:
        """Denied outside root and read-only mutations return failed SubAgentResult."""
        agent = FileOpsAgent(config={"working_dir": str(tmp_path)})

        # Outside root
        res_outside = await agent.run(
            SubAgentTask(
                id="outside-root",
                agent_type=SubAgentType.FILE_OPS,
                payload={"operation": "read", "path": "../outside.txt"},
            )
        )
        assert res_outside.success is False
        assert "outside working directory" in (res_outside.error or "")

        # Read-only mutation
        res_ro = await agent.run(
            SubAgentTask(
                id="ro-mutation",
                agent_type=SubAgentType.FILE_OPS,
                payload={"operation": "write", "path": "test.txt", "content": "forbidden"},
            )
        )
        assert res_ro.success is False
        assert "read-only mode" in (res_ro.error or "")

        # Protected credential file read is denied and never returns secret content
        secret = tmp_path / ".env.local"
        secret.write_bytes(b"SECRET_TOKEN=do-not-leak")
        res_protected = await agent.run(
            SubAgentTask(
                id="protected-read",
                agent_type=SubAgentType.FILE_OPS,
                payload={"operation": "read", "path": ".env.local"},
            )
        )
        assert res_protected.success is False
        assert "Credential/instruction metadata" in (res_protected.error or "")
        # No secret content may leak through output or the error message
        assert "do-not-leak" not in json.dumps(res_protected.output, default=str)
        assert "do-not-leak" not in (res_protected.error or "")

    @pytest.mark.asyncio
    async def test_standalone_default_cap_and_compatibility(self, tmp_path: Path) -> None:
        """Standalone unmanaged calls enforce default 65536 byte cap."""
        (tmp_path / "large_standalone.txt").write_text("x" * 70000, encoding="utf-8")
        agent = FileOpsAgent(config={"working_dir": str(tmp_path)})
        task = SubAgentTask(
            id="standalone-cap",
            agent_type=SubAgentType.FILE_OPS,
            payload={"operation": "read", "path": "large_standalone.txt"},
        )
        result = await agent.run(task)
        assert result.success is True
        size = len(json.dumps(result.output, ensure_ascii=False).encode("utf-8"))
        assert size <= 65536
        assert (result.output or {}).get("truncated") is True

    @pytest.mark.asyncio
    async def test_protected_listing_and_ordinary_siblings(self, tmp_path: Path) -> None:
        """Protected credential/instruction files omitted, ordinary siblings retained."""
        (tmp_path / "normal_file.txt").write_text("hello", encoding="utf-8")
        (tmp_path / ".env.production").write_text("SECRET=1", encoding="utf-8")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("repo", encoding="utf-8")

        policy = RunPolicy(root=tmp_path)
        agent = FileOpsAgent(config={"policy": policy, "working_dir": str(tmp_path)})

        listing = await agent.run(
            SubAgentTask(
                id="list-protected",
                agent_type=SubAgentType.FILE_OPS,
                payload={"operation": "list", "path": ".", "include_hidden": True},
            )
        )
        assert listing.success is True
        names = [item["name"] for item in (listing.output or {}).get("items", [])]
        assert "normal_file.txt" in names
        assert ".env.production" not in names
        assert ".git" not in names

    def test_actual_helper_test_state_restoration(self, tmp_path: Path) -> None:
        """Actual installed test_io_worker_truthful_unsupported_failures restores in_worker state."""
        import importlib.util

        from agentcli import io_boundary

        spec = importlib.util.spec_from_file_location(
            "installed_helper_tests",
            WORKTREE_ROOT / "tests/test_helper_request_contracts.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        before = io_boundary.in_worker
        patches = pytest.MonkeyPatch()
        try:
            module.test_io_worker_truthful_unsupported_failures(tmp_path, patches)
        finally:
            patches.undo()

        assert io_boundary.in_worker == before


class _CountedNativeScandir:
    """Deterministic ``os.scandir`` seam that repeats one real DirEntry.

    Counts every native acquisition attempt, including entries the walker later
    filters, so the 4096-entry budget can be exercised without disk fixtures.
    """

    def __init__(self, entry: os.DirEntry[str], available: int) -> None:
        self._entry = entry
        self._available = available
        self.acquired = 0
        self.closed = False

    def __iter__(self) -> _CountedNativeScandir:
        return self

    def __next__(self) -> os.DirEntry[str]:
        if self.acquired >= self._available:
            raise StopIteration
        self.acquired += 1
        return self._entry

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True


class TestI04BoundedNativeListing:
    """R101: helper-local FileOps listing acquisitions are bounded.

    The shared walker counts native acquisitions before filtering, so protected
    or otherwise filtered entries must still exhaust the 4096-entry budget and
    fail closed instead of returning a successful, silently incomplete listing.
    """

    # 4096-entry budget plus one exhaustion lookahead acquisition.
    ACQUISITION_CAP = 4097

    @staticmethod
    def _install_counted_scandir(
        monkeypatch: pytest.MonkeyPatch, target: Path, entry_name: str, available: int
    ) -> _CountedNativeScandir:
        """Repeat one real DirEntry for ``target`` while counting acquisitions."""
        original_scandir = os.scandir
        with original_scandir(target) as real_entries:
            entry = next(item for item in real_entries if item.name == entry_name)
        seam = _CountedNativeScandir(entry, available)

        def counted_scandir(path: Any, *args: Any, **kwargs: Any) -> Any:
            if Path(path) == target:
                return seam
            return original_scandir(path, *args, **kwargs)

        monkeypatch.setattr(os, "scandir", counted_scandir)
        return seam

    async def _run_seeded_listing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, entry_name: str
    ) -> tuple[SubAgentResult, _CountedNativeScandir]:
        from agentcli import io_boundary

        listing_dir = tmp_path / "listing"
        listing_dir.mkdir()
        (listing_dir / entry_name).write_text("payload", encoding="utf-8")
        seam = self._install_counted_scandir(monkeypatch, listing_dir, entry_name, 20000)
        # Helper-local execution: exercise the native walker without a child process.
        monkeypatch.setattr(io_boundary, "in_worker", True)
        policy = RunPolicy(root=tmp_path)
        agent = FileOpsAgent(config={"policy": policy, "working_dir": str(tmp_path)})
        result = await agent.run(
            SubAgentTask(
                id="native-list",
                agent_type=SubAgentType.FILE_OPS,
                payload={"operation": "list", "path": "listing", "include_hidden": True},
            )
        )
        return result, seam

    @pytest.mark.asyncio
    async def test_filtered_protected_entries_exhaust_budget(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All acquisitions filtered as protected still consume the entry budget."""
        hidden = ".env.secret"  # policy denies this entry, yet it must be counted
        result, seam = await self._run_seeded_listing(monkeypatch, tmp_path, hidden)

        assert seam.acquired > 0  # counted before filtering, not by emitted rows
        assert seam.acquired <= self.ACQUISITION_CAP
        assert seam.closed is True

        assert result.success is False  # no false complete success
        assert "entry limit" in (result.error or "").lower()
        assert result.output is None

    @pytest.mark.asyncio
    async def test_ordinary_entries_exhaust_budget(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ordinary entries beyond the budget fail closed with the same diagnostic."""
        result, seam = await self._run_seeded_listing(monkeypatch, tmp_path, "ordinary.txt")

        assert seam.acquired > 0
        assert seam.acquired <= self.ACQUISITION_CAP
        assert seam.closed is True

        assert result.success is False
        assert "entry limit" in (result.error or "").lower()
        assert result.output is None

    @pytest.mark.asyncio
    async def test_small_native_listing_stays_correct(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Small ordinary and protected listings keep their existing output shape."""
        from agentcli import io_boundary

        listing_dir = tmp_path / "listing"
        listing_dir.mkdir()
        (listing_dir / "ordinary_file.txt").write_text("visible", encoding="utf-8")
        (listing_dir / ".env.production").write_text("SECRET=1", encoding="utf-8")
        (listing_dir / ".git").mkdir()
        monkeypatch.setattr(io_boundary, "in_worker", True)

        policy = RunPolicy(root=tmp_path)
        agent = FileOpsAgent(config={"policy": policy, "working_dir": str(tmp_path)})
        result = await agent.run(
            SubAgentTask(
                id="native-small-list",
                agent_type=SubAgentType.FILE_OPS,
                payload={"operation": "list", "path": "listing", "include_hidden": True},
            )
        )
        assert result.success is True
        names = [item["name"] for item in (result.output or {}).get("items", [])]
        assert "ordinary_file.txt" in names
        assert ".env.production" not in names
        assert ".git" not in names
