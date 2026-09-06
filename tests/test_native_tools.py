"""Comprehensive tests for Phase 13 Native OpenRouter Function Calling and Legacy Fallback."""

from __future__ import annotations

import json

import httpx
import pytest

from agentcli.config import Config, OpenRouterConfig
from agentcli.openrouter_client import (
    ChatMessage,
    OpenRouterClient,
    OpenRouterError,
)
from agentcli.subagents.base import SubAgentTask, SubAgentType
from agentcli.subagents.planner import PlannerAgent
from agentcli.tools_schema import TOOL_DEFINITIONS, get_tool_definitions


async def async_mock_sleep(t: float) -> None:
    pass


def test_chat_message_to_dict() -> None:
    """Test ChatMessage dictionary serialization with tool calling attributes."""
    msg = ChatMessage(
        role="assistant",
        content="Thinking...",
        name="planner",
        tool_calls=[{"id": "call_123", "type": "function", "function": {"name": "file_ops"}}],
        tool_call_id="call_123",
    )
    d = msg.to_dict()
    assert d["role"] == "assistant"
    assert d["content"] == "Thinking..."
    assert d["name"] == "planner"
    assert d["tool_calls"] == [
        {"id": "call_123", "type": "function", "function": {"name": "file_ops"}}
    ]
    assert d["tool_call_id"] == "call_123"


def test_get_tool_definitions() -> None:
    """Test get_tool_definitions filtering."""
    all_defs = get_tool_definitions()
    assert len(all_defs) == len(TOOL_DEFINITIONS)

    file_defs = get_tool_definitions([SubAgentType.FILE_OPS])
    assert len(file_defs) == 1
    assert file_defs[0]["function"]["name"] == "file_ops"

    str_defs = get_tool_definitions(["shell_execution", "code_analyzer"])
    assert len(str_defs) == 2
    names = {d["function"]["name"] for d in str_defs}
    assert names == {"shell_execution", "code_analyzer"}


@pytest.mark.asyncio
async def test_chat_completion_success(monkeypatch) -> None:
    """Test non-streaming chat_completion basic success."""
    monkeypatch.setenv("DUMMY_KEY", "sk-test-123")

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        assert data["model"] == "test-model"
        assert len(data["messages"]) == 1
        return httpx.Response(
            200,
            json={
                "model": "test-model-served",
                "choices": [{"message": {"role": "assistant", "content": "Completed!"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY_KEY")
    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    resp = await client.chat_completion(
        messages=[ChatMessage(role="user", content="hello")],
        model="test-model",
    )
    assert resp["choices"][0]["message"]["content"] == "Completed!"
    assert client.last_served_model == "test-model-served"
    assert client.last_usage["total_tokens"] == 15
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_completion_with_tool_calls(monkeypatch) -> None:
    """Test chat_completion with tools and tool_calls response."""
    monkeypatch.setenv("DUMMY_KEY", "sk-test-123")

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        assert "tools" in data
        assert data["tools"][0]["function"]["name"] == "file_ops"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_abc",
                                    "type": "function",
                                    "function": {
                                        "name": "file_ops",
                                        "arguments": json.dumps(
                                            {"operation": "read", "path": "main.py"}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY_KEY")
    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    tools = get_tool_definitions([SubAgentType.FILE_OPS])
    resp = await client.chat_completion(
        messages=[ChatMessage(role="user", content="read main.py")],
        model="test-model",
        tools=tools,
    )
    calls = resp["choices"][0]["message"]["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "file_ops"
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_completion_429_retry_and_500_retry(monkeypatch) -> None:
    """Test chat_completion retries on 429 and 500."""
    monkeypatch.setenv("DUMMY_KEY", "sk-test-123")
    monkeypatch.setattr("asyncio.sleep", async_mock_sleep)

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, text="Rate limit exceeded")
        if attempts == 2:
            return httpx.Response(500, text="Internal server error")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "Recovered"}}]},
        )

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY_KEY", max_retries=4)
    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    resp = await client.chat_completion(
        messages=[ChatMessage(role="user", content="retry test")],
    )
    assert resp["choices"][0]["message"]["content"] == "Recovered"
    assert attempts == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_completion_400_raises_openrouter_error(monkeypatch) -> None:
    """Test non-retryable 400 error immediately raises OpenRouterError."""
    monkeypatch.setenv("DUMMY_KEY", "sk-test-123")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Tools are not supported on this model")

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY_KEY")
    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    with pytest.raises(OpenRouterError, match="Tools are not supported"):
        await client.chat_completion(
            messages=[ChatMessage(role="user", content="tools test")],
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_planner_native_tool_calling_success(monkeypatch) -> None:
    """Test PlannerAgent using native function calling when supported."""
    agent = PlannerAgent()
    cfg = Config()
    agent._set_config(cfg)

    class MockClient:
        async def chat_completion(
            self, messages, model=None, models=None, tools=None, tool_choice=None
        ):
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "file_ops",
                                        "arguments": json.dumps(
                                            {"operation": "read", "path": "app.py"}
                                        ),
                                    },
                                },
                                {
                                    "id": "call_2",
                                    "type": "function",
                                    "function": {
                                        "name": "shell_execution",
                                        "arguments": json.dumps({"command": "pytest"}),
                                    },
                                },
                            ],
                        }
                    }
                ]
            }

    monkeypatch.setattr("agentcli.subagents.planner.OpenRouterClient", lambda _: MockClient())

    task = SubAgentTask(
        agent_type=SubAgentType.PLANNER,
        payload={
            "query": "read app.py and run pytest",
            "model": "gpt-4o",
            "available_agents": [SubAgentType.FILE_OPS, SubAgentType.SHELL_EXECUTION],
        },
    )
    result = await agent.run(task)
    assert result.success is True
    plan = result.output["plan"]
    assert len(plan) == 2
    assert plan[0]["agent_type"] == "file_ops"
    assert plan[0]["payload"]["path"] == "app.py"
    assert plan[0]["goal_criterion"] == "app.py"
    assert plan[1]["agent_type"] == "shell_execution"
    assert plan[1]["payload"]["command"] == "pytest"
    assert plan[1]["goal_criterion"] == "completed"


@pytest.mark.asyncio
async def test_planner_native_tool_calling_fallback_to_prompt(monkeypatch) -> None:
    """Test PlannerAgent falls back to legacy prompt when native tool calling raises error."""
    agent = PlannerAgent()
    cfg = Config()
    agent._set_config(cfg)

    class MockClient:
        async def chat_completion(
            self, messages, model=None, models=None, tools=None, tool_choice=None
        ):
            raise OpenRouterError("400: Model does not support tools")

        async def chat_stream(self, messages, model=None, models=None):
            yield '[{"agent_type": "code_analyzer", "payload": {"files": ["lib.py"], "focus": "security"}, "priority": 10, "goal_criterion": "security"}]'

    monkeypatch.setattr("agentcli.subagents.planner.OpenRouterClient", lambda _: MockClient())

    task = SubAgentTask(
        agent_type=SubAgentType.PLANNER,
        payload={
            "query": "analyze lib.py",
            "model": "free-model-no-tools",
        },
    )
    result = await agent.run(task)
    assert result.success is True
    plan = result.output["plan"]
    assert len(plan) == 1
    assert plan[0]["agent_type"] == "code_analyzer"
    assert plan[0]["payload"]["files"] == ["lib.py"]


@pytest.mark.asyncio
async def test_file_ops_append_and_stat(tmp_path) -> None:
    from agentcli.subagents.file_ops import FileOpsAgent

    agent = FileOpsAgent(config={"working_dir": str(tmp_path), "allow_write": True})
    test_file = tmp_path / "notes.txt"

    # Write initial
    w_res = await agent.run(
        SubAgentTask(
            agent_type=SubAgentType.FILE_OPS,
            payload={"operation": "write", "file_path": str(test_file), "content": "Line 1\n"},
        )
    )
    assert w_res.success is True

    # Append
    a_res = await agent.run(
        SubAgentTask(
            agent_type=SubAgentType.FILE_OPS,
            payload={"operation": "append", "target": str(test_file), "content": "Line 2\n"},
        )
    )
    assert a_res.success is True
    assert test_file.read_text(encoding="utf-8") == "Line 1\nLine 2\n"

    # Exists & stat
    s_res = await agent.run(
        SubAgentTask(
            agent_type=SubAgentType.FILE_OPS,
            payload={"operation": "exists", "filename": str(test_file)},
        )
    )
    assert s_res.success is True
    assert s_res.output["exists"] is True
    assert s_res.output["is_file"] is True
    assert s_res.output["size"] == len("Line 1\nLine 2\n")


@pytest.mark.asyncio
async def test_code_analyzer_file_aliases(tmp_path) -> None:
    from agentcli.subagents.code_analyzer import CodeAnalyzerAgent

    py_file = tmp_path / "sample.py"
    py_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    agent = CodeAnalyzerAgent()
    res = await agent.run(
        SubAgentTask(
            agent_type=SubAgentType.CODE_ANALYZER,
            payload={"file": str(py_file)},
        )
    )
    assert res.success is True
    assert "def add" in res.output["prompt"]


@pytest.mark.asyncio
async def test_web_search_query_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    from agentcli.subagents.web_search import WebSearchAgent

    agent = WebSearchAgent()
    monkeypatch.setattr(agent.providers["duckduckgo"], "search", AsyncMock(return_value=[]))

    res = await agent.run(
        SubAgentTask(
            agent_type=SubAgentType.WEB_SEARCH,
            payload={"search_query": "python asyncio"},
        )
    )
    assert res.success is True
    assert res.output["query"] == "python asyncio"


@pytest.mark.asyncio
async def test_consensus_agent_tool_registry() -> None:
    from agentcli.agent.registry import ToolRegistry

    registry = ToolRegistry()
    assert "consensus" in registry.registered_types()

    votes = [
        {"voter_id": "agent-1", "choice": "refactor", "confidence": 0.9, "rationale": "Cleaner code"},
        {"voter_id": "agent-2", "choice": "refactor", "confidence": 0.8, "rationale": "Better modularity"},
        {"voter_id": "agent-3", "choice": "keep", "confidence": 0.5, "rationale": "Less risk"},
    ]

    result = await registry.execute(
        "consensus",
        {"votes": votes, "strategy": "majority"},
    )
    assert result.success is True
    assert result.output["decision"] == "refactor"
    assert result.output["consensus_reached"] is True
    assert result.output["agreement_ratio"] == pytest.approx(2 / 3)
@pytest.mark.asyncio
async def test_file_ops_absolute_directory_list_and_read(tmp_path) -> None:
    from agentcli.subagents.file_ops import FileOpsAgent

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    (external_dir / "item1.txt").write_text("hello", encoding="utf-8")
    (external_dir / "subdir").mkdir()

    agent = FileOpsAgent(config={"working_dir": str(workspace_dir), "allow_write": False})

    # Listing external directory via absolute path is permitted for read-only inspections
    res = await agent.run(
        SubAgentTask(
            agent_type=SubAgentType.FILE_OPS,
            payload={"operation": "list", "path": str(external_dir)},
        )
    )
    assert res.success is True
    names = {it["name"] for it in res.output["items"]}
    assert "item1.txt" in names
    assert "subdir" in names

    # Reading external file via absolute path
    res_read = await agent.run(
        SubAgentTask(
            agent_type=SubAgentType.FILE_OPS,
            payload={"operation": "read", "path": str(external_dir / "item1.txt")},
        )
    )
    assert res_read.success is True
    assert res_read.output["content"] == "hello"


@pytest.mark.asyncio
async def test_planner_extracts_directory_and_windows_paths() -> None:
    from agentcli.subagents.planner import PlannerAgent

    planner = PlannerAgent()

    # Windows drive folder path
    res_win = await planner.run(
        SubAgentTask(
            agent_type=SubAgentType.PLANNER,
            payload={"query": r"list the content of C:\Users\sam\Documents"},
        )
    )
    assert res_win.success is True
    plan_win = res_win.output["plan"]
    assert len(plan_win) == 1
    assert plan_win[0]["agent_type"] == "file_ops"
    assert plan_win[0]["payload"]["operation"] == "list"
    assert plan_win[0]["payload"]["path"] == r"C:\Users\sam\Documents"

    # Prepositional folder path
    res_prep = await planner.run(
        SubAgentTask(
            agent_type=SubAgentType.PLANNER,
            payload={"query": "list files in folder my_custom_dir"},
        )
    )
    assert res_prep.success is True
    plan_prep = res_prep.output["plan"]
    assert len(plan_prep) == 1
    assert plan_prep[0]["payload"]["path"] == "my_custom_dir"
