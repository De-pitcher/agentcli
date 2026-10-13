"""F07 stateful result control metadata at the supported minimum."""

import asyncio
import subprocess
import sys

import pytest

from agentcli.agent.registry import ToolRegistry
from agentcli.policy import RunPolicy
from agentcli.runtime import RunContext


@pytest.mark.asyncio
async def test_tiny_task_result_cap_refuses_launch_before_side_effect(tmp_path):
    script = tmp_path / "append.py"
    script.write_text("from pathlib import Path\nPath('marker').write_text('changed')\n")
    context = RunContext(policy=RunPolicy(root=tmp_path, allow_execution=True, max_output_bytes=16))
    registry = ToolRegistry(context=context)
    try:
        result = await registry.execute(
            "task_manager",
            {
                "action": "run",
                "command": subprocess.list2cmdline([sys.executable, str(script)]),
            },
        )
        assert not result.success and "4096" in result.error
        assert not context.tasks._tasks and not (tmp_path / "marker").exists()
    finally:
        await context.aclose()


@pytest.mark.asyncio
async def test_minimum_task_cap_preserves_owned_id_and_terminal_facts(tmp_path):
    script = tmp_path / "terminal.py"
    script.write_text("raise SystemExit(7)\n")
    context = RunContext(
        policy=RunPolicy(root=tmp_path, allow_execution=True, max_output_bytes=4096)
    )
    registry = ToolRegistry(context=context)
    command = subprocess.list2cmdline([sys.executable, str(script), "x" * 6000])
    try:
        result = await registry.execute("task_manager", {"action": "run", "command": command})
        assert result.success and result.output.get("id")
        owned = context.tasks.get_task(result.output["id"])
        assert owned and owned._reader_task
        await asyncio.wait_for(asyncio.shield(owned._reader_task), 2)
        query = await registry.execute("task_manager", {"action": "status", "task_id": owned.id})
        assert query.output["id"] == owned.id
        assert query.output["status"] == "failed" and query.output["returncode"] == 7
        assert query.output["truncated"] and query.output["evidence_available"] is False
        assert query.metadata["provenance"]["owned_task_id"] == owned.id
        assert query.metadata["provenance"]["execution_outcome"] == "incomplete"
    finally:
        await context.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["direct", "registry"])
async def test_authorization_and_execution_share_one_deadline(tmp_path, monkeypatch, entry):
    from agentcli.subagents.base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

    class Delayed(SubAgent):
        def __init__(self):
            super().__init__(SubAgentType.PLANNER)

        async def run(self, task):
            await asyncio.sleep(0.15)
            return SubAgentResult(task.id, task.agent_type, True, output={"completed": True})

    async def authorize(self, name, payload):
        await asyncio.sleep(0.15)

    monkeypatch.setattr(RunPolicy, "aauthorize", authorize)
    context = RunContext(policy=RunPolicy(root=tmp_path, timeout_seconds=0.2))
    registry = ToolRegistry(context=context)
    registry.register("delayed", Delayed)
    try:
        with context.policy.activate():
            result = await (
                Delayed().run(SubAgentTask(agent_type=SubAgentType.PLANNER))
                if entry == "direct"
                else registry.execute("delayed", {})
            )
        assert not result.success
        assert result.output is None
    finally:
        await context.aclose()


@pytest.mark.asyncio
async def test_truncated_task_list_preserves_each_owned_lifecycle(tmp_path):
    context = RunContext(
        policy=RunPolicy(root=tmp_path, allow_execution=True, max_output_bytes=4096)
    )
    registry = ToolRegistry(context=context)
    owned_tasks = []
    try:
        for code in (0, 7):
            script = tmp_path / f"exit{code}.py"
            script.write_text(f"raise SystemExit({code})\n")
            command = subprocess.list2cmdline([sys.executable, str(script), "x" * 6000])
            result = await registry.execute("task_manager", {"action": "run", "command": command})
            assert result.success and result.output["id"]
            owned = context.tasks.get_task(result.output["id"])
            assert owned and owned._reader_task
            await asyncio.wait_for(asyncio.shield(owned._reader_task), 3)
            owned_tasks.append(owned)
        result = await registry.execute("task_manager", {"action": "list"})
        assert result.success
        rows = {row["id"]: row for row in result.output["tasks"]}
        assert set(rows) == {task.id for task in owned_tasks}
        for task, code in zip(owned_tasks, (0, 7), strict=True):
            assert rows[task.id]["returncode"] == code
            assert rows[task.id]["status"] == ("completed" if code == 0 else "failed")
        assert result.output["truncated"] and result.output["evidence_available"] is False
    finally:
        await context.aclose()
