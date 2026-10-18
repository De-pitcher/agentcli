"""Empirical proof suite for narrow governed shell prototype.

Verifies:
1. P01: Caller shell command constraints (security_mode, allowlist, custom denylist) preserved across owned dispatch.
   - Caller with security_mode="allowlist" and allowlist=["whoami"] denies echo.
   - Caller with denylist=["echo"] denies echo.
   - Permitted commands succeed.
   - Payload attempts to override constructor restrictions are rejected.
2. P02: Strict helper-result validation in the actual parent parsing path (_parse_helper_result).
   - Rejects success="false", non-boolean success, missing/invalid fields, success=True with nonzero exit or error message.
   - Retains supported rejection results and valid builtin/native response shapes.
3. P03: Empirical proof of cancellation, grandchild teardown, above-cap output, and negative mutations:
   - Request cancellation propagates CancelledError.
   - Child and real grandchild process ancestry recorded and verified dead with fail-closed check_alive() before emergency cleanup.
   - Normal exit with retained descendant, timeout, and in-flight server/client shutdown.
   - Output cap enforcement with small cap (512 bytes): stdout and stderr floods, multibyte UTF-8, truncated=True.
   - Strict negative mutation controls rejecting fail-open command grants and missing teardown.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from agentcli import processes
from agentcli.mcp.client import MCPClient
from agentcli.mcp.server import MCPServer
from agentcli.policy import RunPolicy
from agentcli.runtime import RunContext
from agentcli.subagents.base import (
    SubAgentTask,
    SubAgentType,
    _parse_helper_result,
)
from agentcli.subagents.shell import ShellExecutionAgent


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
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


def verify_processes_dead(pids: list[int], max_wait: float = 4.0) -> bool:
    """Shared lifecycle verifier: assert all specified PIDs are dead within max_wait."""
    wait_deadline = time.monotonic() + max_wait
    unconfirmed = set(pids)
    while time.monotonic() < wait_deadline and unconfirmed:
        dead = {p for p in unconfirmed if not check_alive(p)}
        unconfirmed -= dead
        if not unconfirmed:
            return True
        time.sleep(0.05)
    return len(unconfirmed) == 0


# ==============================================================================
# P01: Caller shell command constraints preserved across owned dispatch
# ==============================================================================


@pytest.mark.asyncio
async def test_p01_caller_command_constraints_preserved_across_dispatch(tmp_path: Path) -> None:
    """P01: Preserve actual caller security_mode, allowlist, and custom denylist across dispatch.

    - Caller security_mode="allowlist", allowlist=["whoami"]: denies echo, permits whoami.
    - Caller custom denylist=["echo"]: denies echo, permits whoami.
    - Payload attempts to override security_mode or allowlist are rejected.
    """
    policy = RunPolicy(root=tmp_path, allow_execution=True)

    with policy.activate():
        # 1. Caller in allowlist mode with only 'whoami' allowed
        agent_allowlist = ShellExecutionAgent(
            config={
                "security_mode": "allowlist",
                "allowlist": ["whoami"],
                "working_dir": str(tmp_path),
            }
        )
        # Governed call: echo must be rejected
        res_echo = await agent_allowlist.run(
            SubAgentTask(
                agent_type=SubAgentType.SHELL_EXECUTION,
                payload={"command": "echo should_be_denied"},
            )
        )
        assert res_echo.success is False
        assert "not in allowlist" in (res_echo.error or "").lower()

        # Permitted command 'whoami' must succeed
        res_whoami = await agent_allowlist.run(
            SubAgentTask(
                agent_type=SubAgentType.SHELL_EXECUTION,
                payload={"command": "whoami"},
            )
        )
        assert res_whoami.success is True
        assert res_whoami.output is not None

        # Payload attempt to override allowlist to include 'echo' MUST NOT bypass constructor allowlist
        res_bypass = await agent_allowlist.run(
            SubAgentTask(
                agent_type=SubAgentType.SHELL_EXECUTION,
                payload={
                    "command": "echo bypass_attempt",
                    "allowlist": ["echo", "whoami"],
                    "security_mode": "denylist",
                },
            )
        )
        assert res_bypass.success is False
        err_msg = (res_bypass.error or "").lower()
        assert "not in allowlist" in err_msg or "payload cannot grant capabilities" in err_msg

        # 2. Caller with custom denylist containing 'echo'
        agent_custom_deny = ShellExecutionAgent(
            config={
                "denylist": ["echo"],
                "working_dir": str(tmp_path),
            }
        )
        res_deny_echo = await agent_custom_deny.run(
            SubAgentTask(
                agent_type=SubAgentType.SHELL_EXECUTION,
                payload={"command": "echo custom_denied"},
            )
        )
        assert res_deny_echo.success is False
        assert "is denied" in (res_deny_echo.error or "").lower()

        # Permitted command 'whoami' succeeds
        res_permit = await agent_custom_deny.run(
            SubAgentTask(
                agent_type=SubAgentType.SHELL_EXECUTION,
                payload={"command": "whoami"},
            )
        )
        assert res_permit.success is True


# ==============================================================================
# P02: Strict helper response validation
# ==============================================================================


def test_p02_strict_helper_result_validation() -> None:
    """P02: Test exact _parse_helper_result with malformed, coerced, and valid responses."""
    task_id = "test_task"
    agent_type = SubAgentType.SHELL_EXECUTION

    # 1. Non-dict responses rejected
    with pytest.raises(TypeError, match="expected dict"):
        _parse_helper_result("invalid string", task_id, agent_type)
    with pytest.raises(TypeError, match="expected dict"):
        _parse_helper_result(None, task_id, agent_type)

    # 2. Non-boolean 'success' (e.g. string 'false' or int 1) rejected
    with pytest.raises(ValueError, match="'success' must be strict bool"):
        _parse_helper_result(
            {"success": "false", "output": {"stdout": "bogus", "returncode": 7}, "error": None},
            task_id,
            agent_type,
        )
    with pytest.raises(ValueError, match="'success' must be strict bool"):
        _parse_helper_result({"success": 1, "output": None, "error": None}, task_id, agent_type)

    # 3. success=True with nonzero returncode rejected
    with pytest.raises(ValueError, match="success' is True but returncode is 7"):
        _parse_helper_result(
            {
                "success": True,
                "output": {"stdout": "out", "stderr": "", "returncode": 7, "truncated": False},
                "error": None,
            },
            task_id,
            agent_type,
        )

    # 4. success=True with conflicting error message rejected
    with pytest.raises(ValueError, match="'success' is True but 'error' is present"):
        _parse_helper_result(
            {
                "success": True,
                "output": {"stdout": "out", "stderr": "", "returncode": 0, "truncated": False},
                "error": "conflicting error message",
            },
            task_id,
            agent_type,
        )

    # 5. success=True with missing or non-dict output rejected
    with pytest.raises(ValueError, match="output must be dict on success"):
        _parse_helper_result({"success": True, "output": None, "error": None}, task_id, agent_type)

    # 6. success=True with malformed output field types rejected
    with pytest.raises(ValueError, match="'stdout' must be str"):
        _parse_helper_result(
            {
                "success": True,
                "output": {"stdout": 123, "stderr": "", "returncode": 0, "truncated": False},
                "error": None,
            },
            task_id,
            agent_type,
        )
    with pytest.raises(ValueError, match="'truncated' must be strict bool"):
        _parse_helper_result(
            {
                "success": True,
                "output": {"stdout": "ok", "stderr": "", "returncode": 0, "truncated": "yes"},
                "error": None,
            },
            task_id,
            agent_type,
        )

    # 7. Valid genuine success shape accepted
    valid_success = {
        "success": True,
        "output": {
            "stdout": "grounded_output\n",
            "stderr": "",
            "returncode": 0,
            "truncated": False,
        },
        "error": None,
    }
    res_ok = _parse_helper_result(valid_success, task_id, agent_type)
    assert res_ok.success is True
    assert res_ok.output == valid_success["output"]
    assert res_ok.error is None

    # 8. Valid supported failure shape accepted (output=None)
    valid_failure = {
        "success": False,
        "output": None,
        "error": "Command rejected: Command 'rm' is denied",
    }
    res_fail = _parse_helper_result(valid_failure, task_id, agent_type)
    assert res_fail.success is False
    assert "Command rejected" in (res_fail.error or "")

    # 9. Failure with invalid command output types rejected
    with pytest.raises(ValueError, match="'stdout' must be str"):
        _parse_helper_result(
            {
                "success": False,
                "output": {"stdout": 123, "stderr": "err", "returncode": 3, "truncated": False},
                "error": "failed",
            },
            task_id,
            agent_type,
        )
    with pytest.raises(ValueError, match="'stderr' must be str"):
        _parse_helper_result(
            {
                "success": False,
                "output": {"stdout": "out", "stderr": [], "returncode": 3, "truncated": False},
                "error": "failed",
            },
            task_id,
            agent_type,
        )
    with pytest.raises(ValueError, match="'returncode' must be strict int"):
        _parse_helper_result(
            {
                "success": False,
                "output": {"stdout": "out", "stderr": "err", "returncode": "3", "truncated": False},
                "error": "failed",
            },
            task_id,
            agent_type,
        )
    with pytest.raises(ValueError, match="'truncated' must be strict bool"):
        _parse_helper_result(
            {
                "success": False,
                "output": {"stdout": "out", "stderr": "err", "returncode": 3, "truncated": "false"},
                "error": "failed",
            },
            task_id,
            agent_type,
        )

    # 10. Valid genuine failure shape with command output accepted
    valid_failure_with_output = {
        "success": False,
        "output": {
            "stdout": "",
            "stderr": "syntax error near unexpected token\n",
            "returncode": 2,
            "truncated": False,
        },
        "error": "syntax error near unexpected token\n",
    }
    res_fail_out = _parse_helper_result(valid_failure_with_output, task_id, agent_type)
    assert res_fail_out.success is False
    assert res_fail_out.output == valid_failure_with_output["output"]
    assert res_fail_out.error == "syntax error near unexpected token\n"


# ==============================================================================
# Existing in-process and live subprocess contracts
# ==============================================================================


@pytest.mark.asyncio
async def test_empirical_inprocess_mcp_contracts(tmp_path: Path) -> None:
    """Requirement 1: Verify in-process MCP protocol shell execution contracts."""
    from agentcli.agent.registry import ToolRegistry

    # 1. Default server context: shell execution must be denied before side-effects
    marker_file = tmp_path / "should_not_exist.txt"
    default_server = MCPServer()
    req_default = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "shell_execution",
            "arguments": {
                "command": f"{sys.executable} -c \"import pathlib; pathlib.Path(r'{marker_file}').write_text('bad')\""
            },
        },
    }
    resp_default = await default_server.handle_request(req_default)
    assert resp_default is not None and resp_default.get("result") is not None
    assert resp_default["result"]["isError"] is True
    assert "execution is disabled" in resp_default["result"]["content"][0]["text"].lower()
    assert not marker_file.exists(), "Command must not have run on denied default context"

    # 2. Explicit trusted context allows safe execution with exit code 0
    trusted_context = RunContext(policy=RunPolicy(root=tmp_path, allow_execution=True))
    trusted_registry = ToolRegistry(context=trusted_context)
    trusted_server = MCPServer(registry=trusted_registry)

    req_ok = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "shell_execution",
            "arguments": {"command": f"{sys.executable} -c \"print('grounded_stdout_ok')\""},
        },
    }
    resp_ok = await trusted_server.handle_request(req_ok)
    assert resp_ok is not None and resp_ok.get("result") is not None
    assert resp_ok["result"]["isError"] is False
    assert "grounded_stdout_ok" in resp_ok["result"]["content"][0]["text"]

    # 3. Command denylist rejection retained under trusted context
    req_deny = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "shell_execution",
            "arguments": {"command": "rm -rf /"},
        },
    }
    resp_deny = await trusted_server.handle_request(req_deny)
    assert resp_deny is not None and resp_deny.get("result") is not None
    assert resp_deny["result"]["isError"] is True
    assert "denied" in resp_deny["result"]["content"][0]["text"].lower()

    # 4. Outside-root cwd rejection retained under trusted context
    outside_dir = tmp_path.parent / "outside_cwd_probe"
    outside_dir.mkdir(parents=True, exist_ok=True)
    req_cwd = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "shell_execution",
            "arguments": {
                "command": f"{sys.executable} -c \"print('should_fail')\"",
                "working_dir": str(outside_dir),
            },
        },
    }
    resp_cwd = await trusted_server.handle_request(req_cwd)
    assert resp_cwd is not None and resp_cwd.get("result") is not None
    assert resp_cwd["result"]["isError"] is True
    assert "outside the granted workspace" in resp_cwd["result"]["content"][0]["text"].lower()

    # 5. Nonzero exit code reported truthfully as failure
    req_fail = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "shell_execution",
            "arguments": {
                "command": f"{sys.executable} -c \"import sys; sys.stderr.write('fatal_err'); sys.exit(42)\""
            },
        },
    }
    resp_fail = await trusted_server.handle_request(req_fail)
    assert resp_fail is not None and resp_fail.get("result") is not None
    assert resp_fail["result"]["isError"] is True
    assert "fatal_err" in resp_fail["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_empirical_live_subprocess_ambient_envs_and_flags(tmp_path: Path) -> None:
    """Requirement 2: Live subprocess CLI flag vs ambient environment variables."""
    client_default = MCPClient(
        name="live_default",
        command=sys.executable,
        args=["-m", "agentcli", "mcp"],
        timeout_seconds=10.0,
    )
    try:
        await client_default.connect()
        res = await client_default.call_tool(
            "shell_execution",
            {"command": f"{sys.executable} -c \"print('test')\""},
        )
        assert res.get("isError") is True
        assert "execution is disabled" in res["content"][0]["text"].lower()
    finally:
        await client_default.close()

    for ambient_val in ["1", "true", "True", "0", "false"]:
        client_ambient = MCPClient(
            name=f"live_ambient_{ambient_val}",
            command=sys.executable,
            args=["-m", "agentcli", "mcp"],
            timeout_seconds=10.0,
        )
        old_val = os.environ.get("AGENTCLI_ALLOW_EXECUTION")
        os.environ["AGENTCLI_ALLOW_EXECUTION"] = ambient_val
        try:
            await client_ambient.connect()
            call_res = await client_ambient.call_tool(
                "shell_execution",
                {"command": f"{sys.executable} -c \"print('should_be_denied')\""},
            )
            assert call_res.get("isError") is True, (
                f"Ambient value {ambient_val} must not grant execution"
            )
            assert "execution is disabled" in call_res["content"][0]["text"].lower()
        finally:
            if old_val is None:
                os.environ.pop("AGENTCLI_ALLOW_EXECUTION", None)
            else:
                os.environ["AGENTCLI_ALLOW_EXECUTION"] = old_val
            await client_ambient.close()

    client_flag = MCPClient(
        name="live_explicit_flag",
        command=sys.executable,
        args=["-m", "agentcli", "mcp", "--allow-execution"],
        timeout_seconds=10.0,
    )
    try:
        await client_flag.connect()
        call_res = await client_flag.call_tool(
            "shell_execution",
            {"command": f"{sys.executable} -c \"print('flag_granted_ok')\""},
        )
        assert call_res.get("isError") is False
        assert "flag_granted_ok" in call_res["content"][0]["text"]
    finally:
        await client_flag.close()


# ==============================================================================
# P03: Small output cap, truncation, and multibyte UTF-8 proof
# ==============================================================================


@pytest.mark.asyncio
async def test_p03_small_output_cap_and_multibyte_truncation(tmp_path: Path) -> None:
    """P03: Small valid cap (512 bytes) with output above it.

    Asserts:
    - Retained acquisition limits.
    - stdout and stderr floods above cap are safely bounded.
    - Multibyte UTF-8 characters do not crash or corrupt decoding.
    - Truthful truncated=True and returncode=0 reporting.
    """
    small_cap = 512
    policy = RunPolicy(root=tmp_path, allow_execution=True, max_output_bytes=small_cap)
    agent = ShellExecutionAgent(config={"working_dir": str(tmp_path)})

    with policy.activate():
        # 1. stdout flood of 10,000 ASCII bytes above the 512 cap
        res_flood = await agent.run(
            SubAgentTask(
                agent_type=SubAgentType.SHELL_EXECUTION,
                payload={
                    "command": f"{sys.executable} -c \"import sys; sys.stdout.write('A' * 10000)\""
                },
            )
        )
        assert res_flood.success is True
        assert res_flood.output is not None
        assert res_flood.output.get("truncated") is True
        stdout_len = len(res_flood.output.get("stdout", "").encode("utf-8"))
        assert stdout_len <= small_cap, f"stdout exceeded cap: {stdout_len} > {small_cap}"

        # 2. stderr flood of 10,000 ASCII bytes above cap (with non-zero exit code)
        res_stderr = await agent.run(
            SubAgentTask(
                agent_type=SubAgentType.SHELL_EXECUTION,
                payload={
                    "command": f"{sys.executable} -c \"import sys; sys.stderr.write('E' * 10000); sys.exit(3)\""
                },
            )
        )
        assert res_stderr.success is False
        assert res_stderr.output is not None
        assert res_stderr.output.get("returncode") == 3
        assert res_stderr.output.get("truncated") is True
        stderr_len = len(res_stderr.output.get("stderr", "").encode("utf-8"))
        assert stderr_len <= small_cap, f"stderr exceeded cap: {stderr_len} > {small_cap}"

        # 3. Multibyte UTF-8 flood: Chinese and emoji characters (3-4 bytes per char)
        # Verify decoding does not raise UnicodeDecodeError at truncation boundary
        multibyte_str = "🌟こんにちは世界🚀" * 500  # ~15,000 bytes
        res_mb = await agent.run(
            SubAgentTask(
                agent_type=SubAgentType.SHELL_EXECUTION,
                payload={
                    "command": f"{sys.executable} -c \"import sys; sys.stdout.reconfigure(encoding='utf-8'); sys.stdout.write('{multibyte_str}')\""
                },
            )
        )
        assert res_mb.success is True
        assert res_mb.output is not None
        assert res_mb.output.get("truncated") is True
        mb_bytes = len(res_mb.output.get("stdout", "").encode("utf-8"))
        assert mb_bytes <= small_cap

        # 4. Simultaneous stdout and stderr combined flood above cap
        import json

        res_combined = await agent.run(
            SubAgentTask(
                agent_type=SubAgentType.SHELL_EXECUTION,
                payload={
                    "command": f"{sys.executable} -c \"import sys; sys.stdout.write('A' * 10000); sys.stderr.write('E' * 10000)\""
                },
            )
        )
        assert res_combined.success is True
        assert res_combined.output is not None
        assert res_combined.output.get("truncated") is True
        assert res_combined.output.get("returncode") == 0
        ser_bytes = len(json.dumps(res_combined.output, ensure_ascii=False).encode("utf-8"))
        assert ser_bytes <= small_cap, f"Serialized output exceeded cap: {ser_bytes} > {small_cap}"
        assert res_combined.output.get("stdout") != ""
        assert res_combined.output.get("stderr") != ""

        # 5. Genuine builtin cwd output under cap (256 bytes)
        # Verifies complete output dictionary (including 'cwd' metadata) respects limit and fits within cap
        cap_256 = 256
        policy_cwd = RunPolicy(root=tmp_path, allow_execution=True, max_output_bytes=cap_256)
        with policy_cwd.activate():
            agent_cwd = ShellExecutionAgent(config={"working_dir": str(tmp_path)})
            res_cwd = await agent_cwd.run(
                SubAgentTask(
                    agent_type=SubAgentType.SHELL_EXECUTION,
                    payload={"command": "cd ."},
                )
            )
            assert res_cwd.success is True
            assert res_cwd.output is not None
            assert res_cwd.output.get("returncode") == 0
            assert "cwd" in res_cwd.output
            cwd_ser = len(json.dumps(res_cwd.output, ensure_ascii=False).encode("utf-8"))
            assert cwd_ser <= cap_256, (
                f"Serialized output with cwd exceeded {cap_256}: {cwd_ser} > {cap_256}"
            )

        # 6. Irreducible metadata alone exceeding output limit fails closed cleanly
        # In a deep workspace where metadata alone exceeds limit 128, bound_result fails closed cleanly
        long_dir = tmp_path / ("m" * 150)
        long_dir.mkdir(parents=True, exist_ok=True)
        policy_long = RunPolicy(root=tmp_path, allow_execution=True, max_output_bytes=128)
        with policy_long.activate():
            agent_long = ShellExecutionAgent(config={"working_dir": str(long_dir)})
            res_irreducible = await agent_long.run(
                SubAgentTask(
                    agent_type=SubAgentType.SHELL_EXECUTION,
                    payload={"command": f"cd {long_dir}"},
                )
            )
            assert res_irreducible.success is False
            assert "cannot represent required envelope" in (res_irreducible.error or "").lower()
            assert res_irreducible.output is None


# ==============================================================================
# P03: Cancellation, real grandchild ancestry, and process teardown proof
# ==============================================================================


@pytest.mark.asyncio
async def test_p03_cancellation_and_grandchild_teardown(tmp_path: Path) -> None:
    """P03: Request cancellation propagates CancelledError, and terminates helper, child, and grandchild."""
    helper_pid_file = tmp_path / "helper.pid"
    child_pid_file = tmp_path / "child.pid"
    grandchild_pid_file = tmp_path / "grandchild.pid"

    # Grandchild writes its PID to grandchild_pid_file and sleeps
    grandchild_script = tmp_path / "grandchild_runner.py"
    grandchild_script.write_text(
        f"import os, time, pathlib\n"
        f"_pid_target = pathlib.Path(r'{grandchild_pid_file}'); "
        f"_pid_staging = _pid_target.with_suffix('.tmp'); "
        f"_pid_staging.write_text(str(os.getpid()), encoding='utf-8'); "
        f"os.replace(_pid_staging, _pid_target)\n"
        f"time.sleep(60)\n",
        encoding="utf-8",
    )

    # Child writes its PID and parent (helper) PID to files and spawns grandchild
    child_script = tmp_path / "child_runner.py"
    child_script.write_text(
        f"import os, subprocess, sys, time, pathlib\n"
        f"_pid_target = pathlib.Path(r'{child_pid_file}'); "
        f"_pid_staging = _pid_target.with_suffix('.tmp'); "
        f"_pid_staging.write_text(str(os.getpid()), encoding='utf-8'); "
        f"os.replace(_pid_staging, _pid_target)\n"
        f"_pid_target = pathlib.Path(r'{helper_pid_file}'); "
        f"_pid_staging = _pid_target.with_suffix('.tmp'); "
        f"_pid_staging.write_text(str(os.getppid()), encoding='utf-8'); "
        f"os.replace(_pid_staging, _pid_target)\n"
        f"proc = subprocess.Popen([sys.executable, str(pathlib.Path(r'{grandchild_script}'))])\n"
        f"proc.wait()\n",
        encoding="utf-8",
    )

    policy = RunPolicy(root=tmp_path, allow_execution=True, timeout_seconds=15.0)
    agent = ShellExecutionAgent(config={"working_dir": str(tmp_path)})

    task_future: asyncio.Task | None = None
    child_pid: int | None = None
    grandchild_pid: int | None = None
    helper_pid: int | None = None

    try:
        with policy.activate():
            # Start governed shell execution in background task
            task_future = asyncio.create_task(
                agent.run(
                    SubAgentTask(
                        agent_type=SubAgentType.SHELL_EXECUTION,
                        payload={"command": f"{sys.executable} {child_script}"},
                    )
                )
            )

            # Wait for helper, child, and grandchild PIDs to appear with bounded watchdog
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if (
                    child_pid_file.exists()
                    and grandchild_pid_file.exists()
                    and helper_pid_file.exists()
                ):
                    break
                await asyncio.sleep(0.05)

            assert child_pid_file.exists(), "Child process did not record PID"
            assert grandchild_pid_file.exists(), "Grandchild process did not record PID"
            assert helper_pid_file.exists(), "Helper process did not record parent PID"

            child_pid = int(child_pid_file.read_text().strip())
            grandchild_pid = int(grandchild_pid_file.read_text().strip())
            helper_pid = int(helper_pid_file.read_text().strip())

            assert child_pid > 0
            assert grandchild_pid > 0
            assert helper_pid > 0
            assert child_pid != grandchild_pid
            assert child_pid != helper_pid
            assert helper_pid != os.getpid()

            # Verify all processes are alive while task is running
            assert check_alive(child_pid) is True, f"Child {child_pid} should be alive"
            assert check_alive(grandchild_pid) is True, (
                f"Grandchild {grandchild_pid} should be alive"
            )
            assert check_alive(helper_pid) is True, f"Helper {helper_pid} should be alive"

            # Cancel the pending governed task
            assert not task_future.done(), "Task should be pending before cancellation"
            task_future.cancel()

            # Cancellation must propagate CancelledError within bounded timeout
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task_future, timeout=5.0)

            # Assert helper, child, and grandchild terminate promptly after cancellation using shared verifier
            # Note: on Windows, terminating the helper and its process group terminates helper, child, and grandchild
            assert (
                verify_processes_dead([child_pid, grandchild_pid, helper_pid], max_wait=4.0) is True
            ), (
                f"Descendant processes ({child_pid}, {grandchild_pid}, {helper_pid}) remained alive after cancellation!"
            )
    finally:
        # Emergency cleanup to prevent any orphaned processes on assertion/readiness failure
        if task_future is not None and not task_future.done():
            task_future.cancel()
            try:
                await asyncio.wait_for(task_future, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError, OSError):
                pass
        for p in (child_pid, grandchild_pid, helper_pid):
            if p is not None and check_alive(p):
                if sys.platform == "win32":
                    kill_proc = await asyncio.create_subprocess_exec(
                        "taskkill",
                        "/F",
                        "/T",
                        "/PID",
                        str(p),
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await kill_proc.wait()
                else:
                    try:
                        os.kill(p, 9)
                    except OSError:
                        pass


@pytest.mark.asyncio
async def test_p03_normal_command_exit_retaining_descendant(tmp_path: Path) -> None:
    """P03: Normal command process exit retaining an unmanaged background descendant.

    The child exits with returncode 0 after spawning a long-running background grandchild.
    Governed execution teardown must ensure the orphaned descendant is cleanly reaped.
    """
    grandchild_pid_file = tmp_path / "gc_retain.pid"
    grandchild_script = tmp_path / "gc_retain.py"
    grandchild_script.write_text(
        f"import os, time, pathlib\n"
        f"_pid_target = pathlib.Path(r'{grandchild_pid_file}'); "
        f"_pid_staging = _pid_target.with_suffix('.tmp'); "
        f"_pid_staging.write_text(str(os.getpid()), encoding='utf-8'); "
        f"os.replace(_pid_staging, _pid_target)\n"
        f"time.sleep(60)\n",
        encoding="utf-8",
    )

    # Child spawns grandchild detached from stdio, waits until grandchild records PID, then exits with code 0
    child_script = tmp_path / "child_spawn_and_exit.py"
    child_script.write_text(
        f"import subprocess, sys, time, pathlib\n"
        f"p = subprocess.Popen([sys.executable, str(pathlib.Path(r'{grandchild_script}'))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"while not pathlib.Path(r'{grandchild_pid_file}').exists():\n"
        f"    time.sleep(0.05)\n"
        f"sys.exit(0)\n",
        encoding="utf-8",
    )

    policy = RunPolicy(root=tmp_path, allow_execution=True, timeout_seconds=15.0)
    agent = ShellExecutionAgent(config={"working_dir": str(tmp_path)})

    gc_pid: int | None = None
    try:
        with policy.activate():
            res = await agent.run(
                SubAgentTask(
                    agent_type=SubAgentType.SHELL_EXECUTION,
                    payload={"command": f"{sys.executable} {child_script}"},
                )
            )
            assert res.success is True

            # Wait for grandchild PID to be recorded
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not grandchild_pid_file.exists():
                await asyncio.sleep(0.05)

            assert grandchild_pid_file.exists(), "Grandchild PID was not recorded"
            gc_pid = int(grandchild_pid_file.read_text().strip())
            assert gc_pid > 0

            # Once the governed helper call finishes, any descendant process in the job group is terminated
            assert verify_processes_dead([gc_pid], max_wait=4.0) is True, (
                f"Retained grandchild process {gc_pid} remained alive after normal command exit!"
            )
    finally:
        if gc_pid is not None and check_alive(gc_pid):
            if sys.platform == "win32":
                kill_proc = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/F",
                    "/T",
                    "/PID",
                    str(gc_pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await kill_proc.wait()
            else:
                try:
                    os.kill(gc_pid, 9)
                except OSError:
                    pass


@pytest.mark.asyncio
async def test_p03_abrupt_client_server_shutdown_with_execution_pending(tmp_path: Path) -> None:
    """P03: Abrupt in-flight client/server close while shell execution is pending.

    Connects MCPClient to live subprocess server, starts a long-running command,
    and abruptly closes the client connection mid-flight.
    Verifies that the child process and server process are terminated without orphans.
    """
    pid_file = tmp_path / "inflight_child.pid"
    child_cmd = (
        f'{sys.executable} -c "'
        f"import os, time, pathlib; "
        f"_pid_target = pathlib.Path(r'{pid_file}'); "
        f"_pid_staging = _pid_target.with_suffix('.tmp'); "
        f"_pid_staging.write_text(str(os.getpid()), encoding='utf-8'); "
        f"os.replace(_pid_staging, _pid_target); "
        f'time.sleep(60)"'
    )

    client = MCPClient(
        name="live_abrupt_shutdown",
        command=sys.executable,
        args=["-m", "agentcli", "mcp", "--allow-execution"],
        timeout_seconds=15.0,
    )
    server_pid: int | None = None
    child_pid: int | None = None

    try:
        await client.connect()
        server_pid = client._proc.pid if client._proc else None
        assert server_pid is not None and server_pid > 0

        # Launch in-flight tool call task without awaiting completion
        call_task = asyncio.create_task(client.call_tool("shell_execution", {"command": child_cmd}))

        # Wait for child process to start and record PID
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not pid_file.exists():
            await asyncio.sleep(0.05)

        assert pid_file.exists(), "Child process should have started and written PID"
        child_pid = int(pid_file.read_text().strip())
        assert child_pid > 0
        assert child_pid != server_pid
        assert check_alive(child_pid) is True, "Child process should be alive before abrupt close"

        # Abruptly close the client mid-flight while execution is pending
        await client.close()

        # The pending call task must be cancelled or raise an error
        try:
            await asyncio.wait_for(call_task, timeout=2.0)
        except (TimeoutError, asyncio.CancelledError, RuntimeError, OSError):
            pass

        # Both the in-flight child process and the server process must be dead
        pids_to_check = [p for p in (child_pid, server_pid) if p is not None]
        assert verify_processes_dead(pids_to_check, max_wait=4.0) is True, (
            f"Processes {pids_to_check} remained alive after abrupt client close!"
        )
    finally:
        for p in (child_pid, server_pid):
            if p is not None and check_alive(p):
                if sys.platform == "win32":
                    kill_proc = await asyncio.create_subprocess_exec(
                        "taskkill",
                        "/F",
                        "/T",
                        "/PID",
                        str(p),
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await kill_proc.wait()
                else:
                    try:
                        os.kill(p, 9)
                    except OSError:
                        pass


@pytest.mark.asyncio
async def test_p03_negative_mutations_reject_fail_open_and_missing_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P03: Negative mutation controls:

    1. Mutation that bypasses command validation must be rejected by the actual governed verifier.
    2. Mutation that skips actual teardown must be rejected by the actual governed lifecycle verifier.
    """
    # 1. Negative mutation for command allowlist validation
    agent = ShellExecutionAgent(
        config={
            "security_mode": "allowlist",
            "allowlist": ["whoami"],
            "working_dir": str(tmp_path),
        }
    )

    async def verify_governed_allowlist_denial(a: ShellExecutionAgent) -> bool:
        res = await a.run(
            SubAgentTask(
                agent_type=SubAgentType.SHELL_EXECUTION,
                payload={"command": "echo test"},
            )
        )
        return res.success is False and "not in allowlist" in (res.error or "").lower()

    policy = RunPolicy(root=tmp_path, allow_execution=True)
    with policy.activate():
        # Baseline: genuine governed dispatch denies unauthorized command
        assert await verify_governed_allowlist_denial(agent) is True

        # Mutation: inject fail-open allowlist bypass into agent's configuration
        monkeypatch.setattr(agent, "allowlist", ["whoami", "echo"])
        # The SAME verifier must reject the mutated agent
        assert await verify_governed_allowlist_denial(agent) is False, (
            "Negative mutation: fail-open dispatch mutation must be rejected by the actual governed verifier"
        )

    # 2. Honest liveness control: verify dummy living PID is detected by shared verifier
    dummy_proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(10)"
    )
    try:
        pid = dummy_proc.pid
        assert check_alive(pid) is True

        # Liveness check: verify_processes_dead must return False when a process is still alive
        assert verify_processes_dead([pid], max_wait=0.2) is False, (
            "Liveness control: living process must be detected as alive by process death verifier"
        )
    finally:
        if dummy_proc.returncode is None:
            try:
                dummy_proc.kill()
                await dummy_proc.wait()
            except ProcessLookupError:
                pass

    # 3. Permanent verified actual governed teardown mutation:
    # Verifies both positive cancellation teardown and mutated (skipped) teardown
    # on real governed process ancestry: parent-owned helper launch PID + child PID + grandchild PID.
    async def run_governed_lifecycle_case(mutate_teardown: bool) -> None:
        sub_dir = tmp_path / f"gov_mutation_{'mutated' if mutate_teardown else 'positive'}"
        sub_dir.mkdir(exist_ok=True)
        marker_pids = sub_dir / "pids.json"
        marker_gc = sub_dir / "gc.pid"

        gc_script = sub_dir / "gc.py"
        gc_script.write_text(
            f"import os, time, pathlib\n"
            f"_pid_target = pathlib.Path(r'{marker_gc}'); "
            f"_pid_staging = _pid_target.with_suffix('.tmp'); "
            f"_pid_staging.write_text(str(os.getpid()), encoding='utf-8'); "
            f"os.replace(_pid_staging, _pid_target)\n"
            f"time.sleep(45)\n"
        )

        child_script = sub_dir / "child.py"
        child_script.write_text(
            f"import os, subprocess, sys, pathlib, json, time\n"
            f"p = subprocess.Popen([sys.executable, r'{gc_script}'])\n"
            f"_pid_target = pathlib.Path(r'{marker_pids}'); "
            f"_pid_staging = _pid_target.with_suffix('.tmp'); "
            f"_pid_staging.write_text(json.dumps([os.getppid(), os.getpid(), p.pid]), encoding='utf-8'); "
            f"os.replace(_pid_staging, _pid_target)\n"
            f"time.sleep(45)\n"
        )

        gov_policy = RunPolicy(root=sub_dir, allow_execution=True, timeout_seconds=20)
        gov_agent = ShellExecutionAgent(config={"working_dir": str(sub_dir)})

        orig_terminate = processes.terminate
        orig_tree = processes.ProcessTree
        held_procs: list[tuple[Any, Any]] = []
        tracked_trees: list[Any] = []
        task: asyncio.Task[Any] | None = None
        all_pids: list[int] = []

        def tracking_tree(p_id: int) -> Any:
            tree = orig_tree(p_id)
            tracked_trees.append(tree)
            return tree

        async def skip_terminate(proc: Any, tree: Any) -> None:
            held_procs.append((proc, tree))

        processes.ProcessTree = tracking_tree  # type: ignore[misc,assignment]
        try:
            with gov_policy.activate():
                task = asyncio.create_task(
                    gov_agent.run(
                        SubAgentTask(
                            agent_type=SubAgentType.SHELL_EXECUTION,
                            payload={"command": f"{sys.executable} {child_script}"},
                        )
                    )
                )

                deadline = time.monotonic() + 7.0
                while (
                    not (marker_pids.exists() and marker_gc.exists())
                    and time.monotonic() < deadline
                ):
                    await asyncio.sleep(0.05)

                assert marker_pids.exists() and marker_gc.exists(), (
                    "Governed child and grandchild must start and record PIDs"
                )
                raw_pids = json.loads(marker_pids.read_text())
                executing_gc_pid = int(marker_gc.read_text().strip())
                all_pids = list(dict.fromkeys([*raw_pids, executing_gc_pid]))
                assert len(all_pids) >= 3
                assert all(check_alive(x) for x in all_pids), (
                    "All spawned descendants must be alive"
                )
                assert not task.done(), "Governed task must be pending before cancellation"

                assert len(tracked_trees) > 0, "ProcessTree must have tracked the helper process"
                owned_helper_pid = tracked_trees[-1].pid
                all_pids = list(dict.fromkeys([owned_helper_pid, *all_pids]))
                assert check_alive(owned_helper_pid), "Owned helper launch PID must be alive"

                if mutate_teardown:
                    processes.terminate = skip_terminate  # type: ignore[assignment]

                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                    raise AssertionError("Cancellation swallowed; expected CancelledError")
                except asyncio.CancelledError:
                    pass

                # The SAME verifier verify_processes_dead must accept positive and reject mutation
                verdict = verify_processes_dead(all_pids, max_wait=0.2 if mutate_teardown else 4.0)
                assert verdict is (not mutate_teardown), (
                    f"Shared verifier returned {verdict} for mutate_teardown={mutate_teardown}"
                )

                if mutate_teardown:
                    # Verify mutation held the processes
                    assert len(held_procs) > 0
                    # Restore real terminate and run teardown on held processes
                    processes.terminate = orig_terminate
                    for p_item, t_item in held_procs:
                        await orig_terminate(p_item, t_item)
                    # All PIDs must now be confirmed dead
                    assert verify_processes_dead(all_pids, max_wait=4.0) is True, (
                        "All PIDs must be dead after restoring real teardown"
                    )
        finally:
            processes.terminate = orig_terminate
            processes.ProcessTree = orig_tree  # type: ignore[misc]
            if task is not None and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (asyncio.CancelledError, TimeoutError):
                    pass
            for p_item, t_item in held_procs:
                await orig_terminate(p_item, t_item)
            for p_val in all_pids:
                if check_alive(p_val):
                    if sys.platform == "win32":
                        kp = await asyncio.create_subprocess_exec(
                            "taskkill",
                            "/F",
                            "/T",
                            "/PID",
                            str(p_val),
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await kp.wait()
                    else:
                        try:
                            os.kill(p_val, 9)
                        except OSError:
                            pass

    # Execute both positive teardown and mutated teardown cases
    await run_governed_lifecycle_case(mutate_teardown=False)
    await run_governed_lifecycle_case(mutate_teardown=True)


@pytest.mark.asyncio
async def test_empirical_child_command_teardown_and_pids(tmp_path: Path) -> None:
    """Requirement 2 & 3: Child command descendant teardown on timeout/cancellation and negative controls."""
    pid_file = tmp_path / "long_running_child.pid"
    child_cmd = (
        f'{sys.executable} -c "'
        f"import os, time, pathlib; "
        f"_pid_target = pathlib.Path(r'{pid_file}'); "
        f"_pid_staging = _pid_target.with_suffix('.tmp'); "
        f"_pid_staging.write_text(str(os.getpid()), encoding='utf-8'); "
        f"os.replace(_pid_staging, _pid_target); "
        f'time.sleep(60)"'
    )

    client = MCPClient(
        name="live_teardown",
        command=sys.executable,
        args=["-m", "agentcli", "mcp", "--allow-execution"],
        timeout_seconds=15.0,
    )
    server_pid: int | None = None
    try:
        await client.connect()
        server_pid = client._proc.pid if client._proc else None
        assert server_pid is not None and server_pid > 0

        call_task = asyncio.create_task(
            client.call_tool("shell_execution", {"command": child_cmd, "timeout": 1.0})
        )

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not pid_file.exists():
            await asyncio.sleep(0.05)

        assert pid_file.exists(), "Child process should have started and written PID"
        child_pid = int(pid_file.read_text().strip())
        assert child_pid > 0
        assert child_pid != server_pid
        assert check_alive(child_pid) is True, "Child process should be initially alive"

        res = await call_task
        assert res.get("isError") is True, f"Long running command should fail or time out: {res}"

        wait_deadline = time.monotonic() + 4.0
        child_dead = False
        while time.monotonic() < wait_deadline:
            if not check_alive(child_pid):
                child_dead = True
                break
            await asyncio.sleep(0.05)

        assert child_dead, f"Child process {child_pid} remained alive after execution finished!"
    finally:
        await client.close()
        if server_pid is not None:
            srv_deadline = time.monotonic() + 3.0
            srv_dead = False
            while time.monotonic() < srv_deadline:
                if not check_alive(server_pid):
                    srv_dead = True
                    break
                await asyncio.sleep(0.05)
            assert srv_dead, f"Server process {server_pid} remained alive after client.close()"
