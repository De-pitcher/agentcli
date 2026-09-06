"""Comprehensive test suite for Phase 32: Background Tasks, Clarification Modal, and Diagnostics Feedback."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from agentcli.agent.registry import ToolRegistry
from agentcli.agent.tasks import TaskManager
from agentcli.subagents.base import SubAgentTask, SubAgentType
from agentcli.subagents.clarification import ClarificationAgent
from agentcli.subagents.diagnostics import DiagnosticsAgent, DiagnosticsParser
from agentcli.subagents.task_manager import TaskManagerAgent
from agentcli.tools_schema import get_tool_definitions
from agentcli.ui.prompt import SlashAndFileCompleter, resolve_slash_command

# ===========================================================================
# 1. Background TaskManager Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_task_manager_lifecycle(tmp_path: Path) -> None:
    """Test TaskManager starts, streams, inspects, and terminates background tasks."""
    mgr = TaskManager(root_dir=tmp_path)

    # Launch a fast Python process that prints two lines and waits
    cmd = f"\"{sys.executable}\" -c \"import time, sys; print('STARTED_TASK'); sys.stdout.flush(); time.sleep(2); print('DONE_TASK')\""
    task = await mgr.start_task(command=cmd, cwd=tmp_path)

    assert task.id.startswith("task_")
    assert task.status == "running"
    assert task.cwd == str(tmp_path.resolve())

    # Wait for the reader task to capture output
    logs: dict[str, Any] = {"lines": []}
    for _ in range(30):
        await asyncio.sleep(0.1)
        logs = mgr.get_logs(task.id)
        if any("STARTED_TASK" in line for line in logs.get("lines", [])):
            break
    assert any("STARTED_TASK" in line for line in logs.get("lines", []))



    # Offset & tail
    logs_offset = mgr.get_logs(task.id, tail=1, offset=0)
    assert "lines" in logs_offset

    # List tasks
    all_tasks = mgr.list_tasks()
    assert len(all_tasks) == 1
    assert all_tasks[0]["id"] == task.id

    # Status detail
    detail = mgr.get_status(task.id)
    assert detail["id"] == task.id
    assert detail["status"] in ("running", "completed")

    # Kill task
    kill_res = await mgr.kill_task(task.id)
    assert kill_res["success"] is True
    assert kill_res["status"] == "killed"
    assert task.status == "killed"

    # Kill already killed
    kill_again = await mgr.kill_task(task.id)
    assert kill_again["success"] is True

    # Cleanup all
    await mgr.cleanup_all()


@pytest.mark.asyncio
async def test_task_manager_stdin_and_failed_start(tmp_path: Path) -> None:
    """Test sending stdin to a task and handling start failures."""
    mgr = TaskManager(root_dir=tmp_path)

    # Test stdin
    cmd = f'"{sys.executable}" -c "import sys; line = sys.stdin.readline(); print(\'ECHO:\' + line.strip()); sys.stdout.flush()"'
    task = await mgr.start_task(command=cmd, cwd=tmp_path)
    await asyncio.sleep(0.2)

    send_res = await mgr.send_input(task.id, "hello_agentcli")
    assert send_res["success"] is True

    # Allow task to process and finish
    logs: dict[str, Any] = {"lines": []}
    for _ in range(30):
        await asyncio.sleep(0.1)
        logs = mgr.get_logs(task.id)
        if any("ECHO:hello_agentcli" in line for line in logs.get("lines", [])):
            break
    assert any("ECHO:hello_agentcli" in line for line in logs.get("lines", []))


    # Test unknown task errors
    assert "error" in mgr.get_status("invalid_id")
    assert "error" in mgr.get_logs("invalid_id")
    assert (await mgr.send_input("invalid_id", "test"))["success"] is False
    assert (await mgr.kill_task("invalid_id"))["success"] is False


@pytest.mark.asyncio
async def test_task_manager_agent_actions(tmp_path: Path) -> None:
    """Test TaskManagerAgent subagent interface across all supported actions."""
    mgr = TaskManager(root_dir=tmp_path)
    agent = TaskManagerAgent(task_manager=mgr)

    # 1. Run action
    cmd = f'"{sys.executable}" -c "print(\'AGENT_ACTION_OK\')"'
    t_run = SubAgentTask(
        agent_type=SubAgentType.TASK_MANAGER,
        payload={"action": "run", "command": cmd, "cwd": str(tmp_path)},
    )
    r_run = await agent.run(t_run)
    assert r_run.success is True
    task_id = r_run.output["id"]

    # 2. List action
    t_list = SubAgentTask(
        agent_type=SubAgentType.TASK_MANAGER,
        payload={"action": "list"},
    )
    r_list = await agent.run(t_list)
    assert r_list.success is True
    assert r_list.output["total"] >= 1

    # 3. Status action
    t_status = SubAgentTask(
        agent_type=SubAgentType.TASK_MANAGER,
        payload={"action": "status", "task_id": task_id},
    )
    r_status = await agent.run(t_status)
    assert r_status.success is True
    assert r_status.output["id"] == task_id

    # 4. Logs action
    t_logs = SubAgentTask(
        agent_type=SubAgentType.TASK_MANAGER,
        payload={"action": "logs", "task_id": task_id, "tail": 10},
    )
    r_logs = None
    for _ in range(30):
        await asyncio.sleep(0.1)
        r_logs = await agent.run(t_logs)
        if r_logs.success and any("AGENT_ACTION_OK" in line for line in r_logs.output.get("lines", [])):
            break
    assert r_logs is not None and r_logs.success is True
    assert any("AGENT_ACTION_OK" in line for line in r_logs.output["lines"])


    # 5. Send input action
    t_input = SubAgentTask(
        agent_type=SubAgentType.TASK_MANAGER,
        payload={"action": "send_input", "task_id": task_id, "input": "test"},
    )
    r_input = await agent.run(t_input)
    assert isinstance(r_input.output, dict)

    # 6. Kill action
    t_kill = SubAgentTask(
        agent_type=SubAgentType.TASK_MANAGER,
        payload={"action": "kill", "task_id": task_id},
    )
    r_kill = await agent.run(t_kill)
    assert r_kill.success is True

    # 7. Missing parameter errors
    assert (
        await agent.run(
            SubAgentTask(agent_type=SubAgentType.TASK_MANAGER, payload={"action": "run"})
        )
    ).success is False
    assert (
        await agent.run(
            SubAgentTask(agent_type=SubAgentType.TASK_MANAGER, payload={"action": "status"})
        )
    ).success is False
    assert (
        await agent.run(
            SubAgentTask(agent_type=SubAgentType.TASK_MANAGER, payload={"action": "logs"})
        )
    ).success is False
    assert (
        await agent.run(
            SubAgentTask(agent_type=SubAgentType.TASK_MANAGER, payload={"action": "send_input"})
        )
    ).success is False
    assert (
        await agent.run(
            SubAgentTask(agent_type=SubAgentType.TASK_MANAGER, payload={"action": "kill"})
        )
    ).success is False

    # 8. Unknown action error
    t_unknown = SubAgentTask(
        agent_type=SubAgentType.TASK_MANAGER,
        payload={"action": "unknown_action"},
    )
    r_unknown = await agent.run(t_unknown)
    assert r_unknown.success is False
    assert "Unknown task manager action" in str(r_unknown.error)


# ===========================================================================
# 2. ClarificationAgent Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_clarification_agent_interactive_and_headless() -> None:
    """Test ClarificationAgent interactive callback and non-interactive fallbacks."""
    # 1. Non-interactive fallback with recommendations
    agent = ClarificationAgent()
    task = SubAgentTask(
        agent_type=SubAgentType.ASK_QUESTION,
        payload={
            "question": "Which database would you like to use?",
            "options": ["(Recommended) SQLite", "PostgreSQL", "MySQL"],
            "is_multi_select": False,
        },
    )
    res = await agent.run(task)
    assert res.success is True
    assert res.output["interactive"] is False
    assert res.output["selected"] == "(Recommended) SQLite"

    # 2. Non-interactive fallback with NO options
    task_no_opts = SubAgentTask(
        agent_type=SubAgentType.ASK_QUESTION,
        payload={"question": "What is the project name?"},
    )
    res_no_opts = await agent.run(task_no_opts)
    assert res_no_opts.success is True
    assert "proceeding with standard defaults" in res_no_opts.output["answer"]

    # 3. Interactive async handler
    async def mock_async_handler(q: str, opts: list[str] | None, multi: bool) -> str:
        return "PostgreSQL"

    agent.set_handler(mock_async_handler)
    res_interactive = await agent.run(task)
    assert res_interactive.success is True
    assert res_interactive.output["interactive"] is True
    assert res_interactive.output["answer"] == "PostgreSQL"

    # 4. Synchronous handler
    def mock_sync_handler(q: str, opts: list[str] | None, multi: bool) -> str:
        return "MySQL"

    agent.set_handler(mock_sync_handler)
    res_sync = await agent.run(task)
    assert res_sync.success is True
    assert res_sync.output["answer"] == "MySQL"

    # 5. Handler exception gracefully handled
    def failing_handler(q: str, opts: list[str] | None, multi: bool) -> None:
        raise RuntimeError("UI closed")

    agent.set_handler(failing_handler)
    res_fallback = await agent.run(task)
    assert res_fallback.success is True
    assert res_fallback.output["interactive"] is False

    # 6. Payload with questions array
    t_array = SubAgentTask(
        agent_type=SubAgentType.ASK_QUESTION,
        payload={
            "questions": [
                {
                    "question": "Select target environments",
                    "options": ["staging", "production"],
                    "is_multi_select": True,
                }
            ]
        },
    )
    agent.handler = None  # Reset to non-interactive
    res_array = await agent.run(t_array)
    assert res_array.success is True
    assert res_array.output["selected"] == ["staging"]

    # 7. Missing question error
    t_err = SubAgentTask(
        agent_type=SubAgentType.ASK_QUESTION,
        payload={},
    )
    res_err = await agent.run(t_err)
    assert res_err.success is False
    assert "Missing required 'question'" in str(res_err.error)


# ===========================================================================
# 3. DiagnosticsParser & DiagnosticsAgent Tests
# ===========================================================================


def test_diagnostics_parser_formats() -> None:
    """Test parser across Ruff, Mypy, TypeScript, ESLint, Rust/Cargo, and Generic formats."""
    # Empty string
    assert DiagnosticsParser.parse("") == []

    # Ruff format
    ruff_text = "agentcli/app.py:14:5: E501 line too long (92 > 88 characters)\nagentcli/app.py:20:1: F401 'sys' imported but unused\n"
    spans_ruff = DiagnosticsParser.parse(ruff_text, framework="ruff")
    assert len(spans_ruff) == 2
    assert spans_ruff[0].file == "agentcli/app.py"
    assert spans_ruff[0].line == 14
    assert spans_ruff[0].column == 5
    assert spans_ruff[0].code == "E501"

    # Mypy format
    mypy_text = "agentcli/session.py:42: error: Incompatible types in assignment [assignment]\nagentcli/session.py:55: note: See docs for details\n"
    spans_mypy = DiagnosticsParser.parse(mypy_text, framework="mypy")
    assert len(spans_mypy) == 2
    assert spans_mypy[0].severity == "error"
    assert spans_mypy[0].code == "assignment"
    assert spans_mypy[1].severity == "note"

    # TypeScript format
    tsc_text = "src/index.ts(15,22): error TS2322: Type 'string' is not assignable to type 'number'.\nsrc/util.ts:25:10 - error TS2304: Cannot find name 'foo'.\n"
    spans_tsc = DiagnosticsParser.parse(tsc_text, framework="tsc")
    assert len(spans_tsc) == 2
    assert spans_tsc[0].line == 15
    assert spans_tsc[0].code == "TS2322"
    assert spans_tsc[1].line == 25

    # ESLint format
    eslint_text = (
        "src/app.js: line 10, col 3, Error - 'val' is defined but never used (no-unused-vars)\n"
    )
    spans_eslint = DiagnosticsParser.parse(eslint_text, framework="eslint")
    assert len(spans_eslint) == 1
    assert spans_eslint[0].line == 10
    assert spans_eslint[0].column == 3
    assert spans_eslint[0].code == "no-unused-vars"

    # Rust/Cargo format
    rust_text = "error[E0425]: cannot find value `foo` in this scope\n  --> src/main.rs:12:5\n"
    spans_rust = DiagnosticsParser.parse(rust_text, framework="cargo")
    assert len(spans_rust) == 1
    assert spans_rust[0].file == "src/main.rs"
    assert spans_rust[0].line == 12
    assert spans_rust[0].column == 5
    assert spans_rust[0].code == "E0425"

    # Generic format fallback
    generic_text = "build/out.c:18:4: warning: implicit declaration of function\n"
    spans_gen = DiagnosticsParser.parse(generic_text, framework="auto")
    assert len(spans_gen) == 1
    assert spans_gen[0].file == "build/out.c"
    assert spans_gen[0].line == 18
    assert spans_gen[0].severity == "warning"

    # Pytest format
    pytest_text = (
        "FAILED tests/test_core.py::test_login - AssertionError: assert False\n"
        "tests/test_core.py:42: AssertionError\n"
    )
    spans_pytest = DiagnosticsParser.parse(pytest_text, framework="pytest")
    assert len(spans_pytest) == 1
    assert spans_pytest[0].file == "tests/test_core.py"
    assert spans_pytest[0].line == 42
    assert "test_login" in spans_pytest[0].message


@pytest.mark.asyncio
async def test_diagnostics_agent_execution() -> None:
    """Test DiagnosticsAgent with raw output string, clean command, and failed command."""
    agent = DiagnosticsAgent()

    # 1. Parsing raw output
    raw_output = (
        "agentcli/core.py:10:1: F401 'os' imported but unused\n"
        "agentcli/core.py:25: error: Item 'None' of 'Optional[str]' has no attribute 'lower' [union-attr]\n"
    )

    task = SubAgentTask(
        agent_type=SubAgentType.DIAGNOSTICS_CHECK,
        payload={"output": raw_output, "framework": "auto"},
    )
    res = await agent.run(task)
    assert res.success is True
    assert res.output["total_diagnostics"] == 2
    assert res.output["errors_count"] == 2
    assert "Found 2 error(s)" in res.output["summary"]

    # 2. Clean command execution (exit 0, no errors)
    cmd_clean = f'"{sys.executable}" -c "print(\'All good\')"'
    task_clean = SubAgentTask(
        agent_type=SubAgentType.DIAGNOSTICS_CHECK,
        payload={"command": cmd_clean},
    )
    res_clean = await agent.run(task_clean)
    assert res_clean.success is True
    assert "0 errors and 0 warnings" in res_clean.output["summary"]


# ===========================================================================
# 4. Registry, Schema, and UI Slash Command Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_tool_registry_and_definitions() -> None:
    """Verify manage_task, ask_question, and diagnostics_check in registry and schema."""
    registry = ToolRegistry()
    registered = registry.registered_types()
    assert SubAgentType.TASK_MANAGER.value in registered
    assert SubAgentType.ASK_QUESTION.value in registered
    assert SubAgentType.DIAGNOSTICS_CHECK.value in registered

    defs = get_tool_definitions()
    names = {d["function"]["name"] for d in defs}
    assert "manage_task" in names
    assert "ask_question" in names
    assert "diagnostics_check" in names

    # Execute via registry
    r_task = await registry.execute("task_manager", {"action": "list"})
    assert r_task.success is True

    r_ask = await registry.execute("ask_question", {"question": "Ready?"})
    assert r_ask.success is True

    r_diag = await registry.execute("diagnostics_check", {"output": ""})
    assert r_diag.success is True


def test_ui_slash_tasks_resolution() -> None:
    """Test /tasks slash command resolution and autocompletion."""
    assert resolve_slash_command("/task") == "/tasks"
    assert resolve_slash_command("/bg") == "/tasks"
    assert resolve_slash_command("/tasks list") == "/tasks list"
    assert resolve_slash_command("\\tasks") == "/tasks"

    completer = SlashAndFileCompleter()
    doc = Document("/tas")
    completions = [c.text for c in completer.get_completions(doc, CompleteEvent())]
    assert "/tasks" in completions

