"""Actual POSIX nested command ownership; Windows Job paths have other fixtures."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from agentcli.policy import RunPolicy
from agentcli.processes import run_process


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX process-group acceptance requires Linux runner"
)
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "deadline"])
async def test_nested_fixed_helper_command_stays_in_owned_group(tmp_path, mode):
    marker = tmp_path / "nested.pid"
    child = (
        "import os,time; from pathlib import Path; Path("
        + repr(str(marker))
        + ").write_text(str(os.getpid())); time.sleep(20)"
    )
    helper = (
        "from agentcli import io_boundary; io_boundary.in_worker=True; from agentcli.processes import run_process_sync; from agentcli.policy import RunPolicy; import sys; run_process_sync([sys.executable,'-I','-c',"
        + repr(child)
        + "],RunPolicy(root="
        + repr(str(tmp_path))
        + ",allow_execution=True,timeout_seconds=20))"
    )
    operation = asyncio.create_task(
        run_process(
            [sys.executable, "-I", "-c", helper],
            RunPolicy(root=tmp_path, allow_execution=True, timeout_seconds=3),
        )
    )
    try:
        for _ in range(200):
            if marker.exists() and marker.read_text().isdigit():
                break
            await asyncio.sleep(0.01)
        assert marker.exists() and marker.read_text().isdigit()
        pid = int(marker.read_text())
        if mode == "cancel":
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(operation, 5)
        else:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(operation, 5)
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            status = Path(f"/proc/{pid}/stat")
            if status.exists() and status.read_text().split()[2] == "Z":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("nested native command survived helper teardown")
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
