"""Tests for the sub-agents system."""

from __future__ import annotations

import asyncio
from datetime import UTC

import pytest

from agentcli.subagents.base import (
    SubAgent,
    SubAgentConfig,
    SubAgentResult,
    SubAgentStatus,
    SubAgentTask,
    SubAgentType,
)
from agentcli.subagents.bus import Message, MessageBus, MessageType
from agentcli.subagents.code_analyzer import CodeAnalyzerAgent
from agentcli.subagents.file_ops import FileOpsAgent
from agentcli.subagents.planner import PlannerAgent
from agentcli.subagents.shell import ShellExecutionAgent
from agentcli.subagents.spawner import SubAgentPool, SubAgentSpawner
from agentcli.subagents.web_search import WebSearchAgent


class DummySubAgent(SubAgent):
    """Simple test sub-agent."""

    def __init__(self, delay: float = 0.0, should_fail: bool = False) -> None:

        super().__init__(SubAgentType.CODE_ANALYZER)

        self.delay = delay

        self.should_fail = should_fail

    async def run(self, task: SubAgentTask) -> SubAgentResult:

        if self.delay > 0:
            await asyncio.sleep(self.delay)

        if self.should_fail:
            raise RuntimeError("Dummy failure")

        return SubAgentResult(
            task_id=task.id,
            agent_type=self.agent_type,
            success=True,
            output={"result": "ok"},
        )


class TestBaseClasses:
    """Tests for base classes and lifecycle hooks."""

    def test_subagent_task_creation_and_coercion(self) -> None:

        task = SubAgentTask(agent_type=SubAgentType.CODE_ANALYZER, payload={"key": "value"})

        assert task.id is not None

        assert task.agent_type == SubAgentType.CODE_ANALYZER

        assert task.payload == {"key": "value"}

        assert task.created_at.tzinfo == UTC

    def test_subagent_result_creation_and_coercion(self) -> None:

        result = SubAgentResult(
            task_id="task-123",
            agent_type=SubAgentType.FILE_OPS,
            success=True,
            output={"key": "value"},
        )

        assert result.task_id == "task-123"

        assert result.agent_type == SubAgentType.FILE_OPS

        assert result.success is True

        assert result.output == {"key": "value"}

        assert result.started_at.tzinfo == UTC

    def test_subagent_config_defaults(self) -> None:

        config = SubAgentConfig()

        assert config.enabled is True

        assert config.max_concurrent == 3

        assert config.idle_timeout_seconds == 300.0

        assert config.max_concurrent_global == 10

        assert config.specific_config == {}

    @pytest.mark.asyncio
    async def test_subagent_lifecycle_hooks(self) -> None:

        agent = DummySubAgent()

        assert agent.agent_id.startswith("code_analyzer-")

        assert agent.is_idle()

        assert agent.idle_duration() is None

        assert agent.execution_duration() is None

        task = SubAgentTask(agent_type=SubAgentType.CODE_ANALYZER, payload={})

        await agent.on_start(task)

        assert agent.status == SubAgentStatus.RUNNING

        assert agent.current_task_id == task.id

        assert agent.execution_duration() is not None

        res = SubAgentResult(task_id=task.id, agent_type=agent.agent_type, success=True)

        await agent.on_complete(task, res)

        assert agent.status == SubAgentStatus.COMPLETED

        await agent.on_idle()

        assert agent.is_idle()

        assert agent.current_task_id is None

        assert agent.idle_duration() is not None

        await agent.on_failure(task, ValueError("boom"))

        assert agent.status == SubAgentStatus.FAILED

        # Test kill

        agent._task = asyncio.create_task(asyncio.sleep(10))

        await agent.kill()

        assert agent.status == SubAgentStatus.KILLED


class TestMessageBus:
    """Tests for the message bus."""

    @pytest.mark.asyncio
    async def test_publish_subscribe(self) -> None:

        bus = MessageBus()

        received: list[Message] = []

        async def handler(msg: Message) -> None:

            received.append(msg)

        bus.subscribe(MessageType.TASK_SUBMIT, handler)

        msg = Message(type=MessageType.TASK_SUBMIT, source="test", payload={"data": "test"})

        await bus.publish(msg)

        assert len(received) == 1

        assert received[0].payload["data"] == "test"

    @pytest.mark.asyncio
    async def test_publish_targeted(self) -> None:

        bus = MessageBus()

        received: list[Message] = []

        async def handler(msg: Message) -> None:

            received.append(msg)

        bus.subscribe(MessageType.TASK_SUBMIT, handler, target="agent1")

        msg = Message(type=MessageType.TASK_SUBMIT, source="test", target="agent1", payload={})

        await bus.publish(msg)

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_broadcast_and_unsubscribe(self) -> None:

        bus = MessageBus()

        received: list[Message] = []

        async def handler(msg: Message) -> None:

            received.append(msg)

        bus.subscribe_broadcast(handler)

        b_msg = bus.broadcast(MessageType.CUSTOM, {"k": "v"}, source="src")

        await bus.publish(b_msg)

        assert len(received) == 1

        # Unsubscribe and verify no more messages

        bus.unsubscribe(handler)

        await bus.publish(b_msg)

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_request_response(self) -> None:

        bus = MessageBus()

        async def responder(msg: Message) -> None:

            if msg.type == MessageType.TASK_SUBMIT:
                reply = Message(
                    type=MessageType.TASK_RESULT,
                    source="worker",
                    payload={"status": "done"},
                    correlation_id=msg.id,
                )

                await bus.publish(reply)

        bus.subscribe(MessageType.TASK_SUBMIT, responder)

        req = Message(type=MessageType.TASK_SUBMIT, source="client", payload={"work": 1})

        resp = await bus.request_response(req, expected_type=MessageType.TASK_RESULT, timeout=2.0)

        assert resp is not None

        assert resp.payload == {"status": "done"}

    @pytest.mark.asyncio
    async def test_request_response_timeout(self) -> None:

        bus = MessageBus()

        req = Message(type=MessageType.TASK_SUBMIT, source="client")

        resp = await bus.request_response(req, expected_type=MessageType.TASK_RESULT, timeout=0.05)

        assert resp is None

    @pytest.mark.asyncio
    async def test_handler_error_and_timeout_do_not_crash_bus(self) -> None:

        bus = MessageBus(handler_timeout=0.05)

        async def slow_handler(_: Message) -> None:

            await asyncio.sleep(0.2)

        async def failing_handler(_: Message) -> None:

            raise RuntimeError("handler crashed")

        bus.subscribe(MessageType.CUSTOM, slow_handler)

        bus.subscribe(MessageType.CUSTOM, failing_handler)

        msg = Message(type=MessageType.CUSTOM)

        # Should not raise

        await bus.publish(msg)


class TestCodeAnalyzer:
    """Tests for CodeAnalyzerAgent."""

    @pytest.mark.asyncio
    async def test_code_analyzer_no_files(self) -> None:

        agent = CodeAnalyzerAgent()

        task = SubAgentTask(agent_type=SubAgentType.CODE_ANALYZER, payload={})

        result = await agent.run(task)

        assert result.success is False

        assert "No files provided" in str(result.error)

    @pytest.mark.asyncio
    async def test_code_analyzer_with_files(self, tmp_path: pytest.TempPathFactory) -> None:

        agent = CodeAnalyzerAgent()

        f = tmp_path / "test.py"  # type: ignore[operator]

        f.write_text("def hello():\n    print('hello')", encoding="utf-8")

        task = SubAgentTask(
            agent_type=SubAgentType.CODE_ANALYZER,
            payload={"files": [str(f)], "focus": "security"},
        )

        result = await agent.run(task)

        assert result.success is True

        assert result.output["focus"] == "security"

        assert len(result.output["files_analyzed"]) == 1

    @pytest.mark.asyncio
    async def test_code_analyzer_file_read_error(self) -> None:

        agent = CodeAnalyzerAgent()

        task = SubAgentTask(
            agent_type=SubAgentType.CODE_ANALYZER,
            payload={"files": ["/nonexistent/path/never_exists.py"]},
        )

        result = await agent.run(task)

        assert result.success is False

        assert "Failed to read" in str(result.error)


class TestFileOps:
    """Tests for FileOpsAgent."""

    @pytest.mark.asyncio
    async def test_missing_operation_and_path(self) -> None:

        agent = FileOpsAgent()

        t1 = SubAgentTask(agent_type=SubAgentType.FILE_OPS, payload={})

        r1 = await agent.run(t1)

        assert r1.success is False

        assert "No operation" in str(r1.error)

        t2 = SubAgentTask(agent_type=SubAgentType.FILE_OPS, payload={"operation": "read"})

        r2 = await agent.run(t2)

        assert r2.success is False

        assert "No path" in str(r2.error)

    @pytest.mark.asyncio
    async def test_file_ops_crud(self, tmp_path: pytest.TempPathFactory) -> None:

        working_dir = str(tmp_path)

        agent = FileOpsAgent(config={"working_dir": working_dir})

        file_path = "subdir/test.txt"

        # Write

        t_write = SubAgentTask(
            agent_type=SubAgentType.FILE_OPS,
            payload={"operation": "write", "path": file_path, "content": "caf├⌐"},
        )

        r_write = await agent.run(t_write)

        assert r_write.success is True

        assert r_write.output["bytes_written"] > 0

        # Read

        t_read = SubAgentTask(
            agent_type=SubAgentType.FILE_OPS,
            payload={"operation": "read", "path": file_path},
        )

        r_read = await agent.run(t_read)

        assert r_read.success is True

        assert r_read.output["content"] == "caf├⌐"

        # List

        t_list = SubAgentTask(
            agent_type=SubAgentType.FILE_OPS,
            payload={"operation": "list", "path": "subdir"},
        )

        r_list = await agent.run(t_list)

        assert r_list.success is True

        assert len(r_list.output["items"]) == 1

        # Delete

        t_del = SubAgentTask(
            agent_type=SubAgentType.FILE_OPS,
            payload={"operation": "delete", "path": file_path},
        )

        r_del = await agent.run(t_del)

        assert r_del.success is True

        assert r_del.output["deleted"] is True

        # Mkdir

        t_mkdir = SubAgentTask(
            agent_type=SubAgentType.FILE_OPS,
            payload={"operation": "mkdir", "path": "new_folder"},
        )

        r_mkdir = await agent.run(t_mkdir)

        assert r_mkdir.success is True

    @pytest.mark.asyncio
    async def test_file_ops_path_traversal_blocked(self, tmp_path: pytest.TempPathFactory) -> None:

        agent = FileOpsAgent(config={"working_dir": str(tmp_path)})

        task = SubAgentTask(
            agent_type=SubAgentType.FILE_OPS,
            payload={"operation": "read", "path": "../outside.txt"},
        )

        result = await agent.run(task)

        assert result.success is False

        assert "outside working directory" in str(result.error)

    @pytest.mark.asyncio
    async def test_file_ops_unknown_operation(self, tmp_path: pytest.TempPathFactory) -> None:

        agent = FileOpsAgent(config={"working_dir": str(tmp_path)})

        task = SubAgentTask(
            agent_type=SubAgentType.FILE_OPS,
            payload={"operation": "unknown_op", "path": "file.txt"},
        )

        result = await agent.run(task)

        assert result.success is False

        assert "Unknown operation" in str(result.error)


class TestShellExecution:
    """Tests for ShellExecutionAgent."""

    @pytest.mark.asyncio
    async def test_empty_command(self) -> None:

        agent = ShellExecutionAgent()

        task = SubAgentTask(agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": ""})

        result = await agent.run(task)

        assert result.success is False

        assert "No command" in str(result.error)

    @pytest.mark.asyncio
    async def test_allowlist_mode(self) -> None:

        agent = ShellExecutionAgent(config={"security_mode": "allowlist", "allowlist": ["python"]})

        t_allow = SubAgentTask(
            agent_type=SubAgentType.SHELL_EXECUTION,
            payload={"command": 'python -c "print(42)"'},
        )

        r_allow = await agent.run(t_allow)

        assert r_allow.success is True

        assert "42" in r_allow.output["stdout"]

        t_block = SubAgentTask(
            agent_type=SubAgentType.SHELL_EXECUTION,
            payload={"command": "curl https://example.com"},
        )

        r_block = await agent.run(t_block)

        assert r_block.success is False

        assert "not in allowlist" in str(r_block.error)

    @pytest.mark.asyncio
    async def test_denylist_mode(self) -> None:

        agent = ShellExecutionAgent(
            config={"security_mode": "denylist", "denylist": ["rm", "shutdown"]}
        )

        t_block = SubAgentTask(
            agent_type=SubAgentType.SHELL_EXECUTION,
            payload={"command": "rm -rf something"},
        )

        r_block = await agent.run(t_block)

        assert r_block.success is False

        assert "denied" in str(r_block.error)

    @pytest.mark.asyncio
    async def test_default_denylist_blocks_destructive_commands(self) -> None:

        # Without explicit config, DEFAULT_DENYLIST must be enforced

        agent = ShellExecutionAgent()

        for cmd in ["rm -rf /", "del /f *", "powershell -c ls", "bash -c whoami"]:
            task = SubAgentTask(
                agent_type=SubAgentType.SHELL_EXECUTION,
                payload={"command": cmd},
            )

            res = await agent.run(task)

            assert res.success is False

            assert "is denied" in str(res.error)

    @pytest.mark.asyncio
    async def test_dangerous_env_vars_blocked(self) -> None:

        agent = ShellExecutionAgent(config={"security_mode": "allowlist", "allowlist": ["python"]})

        task = SubAgentTask(
            agent_type=SubAgentType.SHELL_EXECUTION,
            payload={
                "command": 'python -c "print(1)"',
                "env": {"LD_PRELOAD": "/fake/path.so"},
            },
        )

        result = await agent.run(task)

        assert result.success is False

        assert "Dangerous environment variable override rejected" in str(result.error)

    @pytest.mark.asyncio
    async def test_timeout_and_output_bounding(self) -> None:

        agent = ShellExecutionAgent(config={"command_timeout": 0.1, "max_output_bytes": 10})

        t_timeout = SubAgentTask(
            agent_type=SubAgentType.SHELL_EXECUTION,
            payload={"command": 'python -c "import time; time.sleep(1)"'},
        )

        r_timeout = await agent.run(t_timeout)

        assert r_timeout.success is False

        assert "timed out" in str(r_timeout.error)


class TestWebSearch:
    """Tests for WebSearchAgent."""

    @pytest.mark.asyncio
    async def test_empty_query(self) -> None:

        agent = WebSearchAgent()

        task = SubAgentTask(agent_type=SubAgentType.WEB_SEARCH, payload={})

        result = await agent.run(task)

        assert result.success is False

        assert "No search query" in str(result.error)

    @pytest.mark.asyncio
    async def test_search_works(self) -> None:
        agent = WebSearchAgent()

        task = SubAgentTask(
            agent_type=SubAgentType.WEB_SEARCH,
            payload={
                "query": "python asyncio",
                "provider": "duckduckgo",
            },
        )

        result = await agent.run(task)

        assert result.success is True
        assert result.output["count"] > 0
        assert len(result.output["results"]) > 0
        assert "provider" in result.output


class TestPlanner:
    """Tests for PlannerAgent."""

    @pytest.mark.asyncio
    async def test_planner_empty_query(self) -> None:

        agent = PlannerAgent()

        task = SubAgentTask(agent_type=SubAgentType.PLANNER, payload={})

        result = await agent.run(task)

        assert result.success is False

        assert "No query provided" in str(result.error)

    @pytest.mark.asyncio
    async def test_planner_heuristic_decomposition(self) -> None:

        agent = PlannerAgent()

        task = SubAgentTask(
            agent_type=SubAgentType.PLANNER,
            payload={
                "query": "Please analyze bug in @app.py, then read test.txt and run 'python main.py'"
            },
        )

        result = await agent.run(task)

        assert result.success is True

        plan = result.output["plan"]

        types = [step["agent_type"] for step in plan]

        assert "code_analyzer" in types

        assert "file_ops" in types

        assert "shell_execution" in types

    @pytest.mark.asyncio
    async def test_planner_restricted_available_agents(self) -> None:

        agent = PlannerAgent()

        task = SubAgentTask(
            agent_type=SubAgentType.PLANNER,
            payload={
                "query": "run 'python main.py'",
                "available_agents": [SubAgentType.CODE_ANALYZER],
            },
        )

        result = await agent.run(task)

        assert result.success is True

        plan = result.output["plan"]

        # Shell execution was not available, so it fell back to code analyzer

        assert all(step["agent_type"] == "code_analyzer" for step in plan)


class TestSpawnerAndPool:
    """Tests for SubAgentPool and SubAgentSpawner."""

    @pytest.mark.asyncio
    async def test_pool_submit_and_idle_reuse(self) -> None:

        bus = MessageBus()

        config = SubAgentConfig(max_concurrent=2, idle_timeout_seconds=300.0)

        pool = SubAgentPool(
            agent_type=SubAgentType.CODE_ANALYZER,
            config=config,
            agent_factory=lambda: DummySubAgent(delay=0.01),
            message_bus=bus,
        )

        t1 = SubAgentTask(agent_type=SubAgentType.CODE_ANALYZER, payload={})

        r1 = await pool.submit_task(t1)

        assert r1.success is True

        # Give small moment to return to idle pool

        await asyncio.sleep(0.02)

        assert len(pool._idle_agents) == 1

        # Second task should reuse idle agent

        t2 = SubAgentTask(agent_type=SubAgentType.CODE_ANALYZER, payload={})

        r2 = await pool.submit_task(t2)

        assert r2.success is True

        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_pool_idle_timeout(self) -> None:

        bus = MessageBus()

        config = SubAgentConfig(max_concurrent=1, idle_timeout_seconds=0.05)

        pool = SubAgentPool(
            agent_type=SubAgentType.CODE_ANALYZER,
            config=config,
            agent_factory=lambda: DummySubAgent(),
            message_bus=bus,
        )

        await pool.start()

        task = SubAgentTask(agent_type=SubAgentType.CODE_ANALYZER, payload={})

        await pool.submit_task(task)

        await asyncio.sleep(0.01)

        assert len(pool._idle_agents) == 1

        # Check idle agents manually

        await pool._check_idle_agents()

        assert len(pool._idle_agents) == 1  # not timed out yet

        await asyncio.sleep(0.06)

        await pool._check_idle_agents()

        assert len(pool._idle_agents) == 0

        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_spawner_lifecycle_and_dispatch(self) -> None:

        bus = MessageBus()

        spawner = SubAgentSpawner(
            config={
                "code_analyzer": SubAgentConfig(max_concurrent=2),
                "planner": SubAgentConfig(max_concurrent=1),
            },
            agent_factories={
                "code_analyzer": lambda: DummySubAgent(),
                "planner": lambda: PlannerAgent(),
            },
            message_bus=bus,
        )

        await spawner.start()

        task = SubAgentTask(agent_type=SubAgentType.CODE_ANALYZER, payload={})

        result = await spawner.submit_task(SubAgentType.CODE_ANALYZER, task)

        assert result.success is True

        p_task = SubAgentTask(agent_type=SubAgentType.PLANNER, payload={"query": "review code"})

        p_result = await spawner.submit_planner_task(p_task)

        assert p_result.success is True

        usage = spawner.get_resource_usage()

        assert "code_analyzer" in usage

        status = await spawner.get_status()

        assert "code_analyzer" in status

        with pytest.raises(ValueError, match="No pool for agent type"):
            await spawner.submit_task(
                SubAgentType.WEB_SEARCH,
                SubAgentTask(agent_type=SubAgentType.WEB_SEARCH),
            )

        await spawner.shutdown()
