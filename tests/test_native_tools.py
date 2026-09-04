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
    RateLimitedError,
)
from agentcli.subagents.base import SubAgentTask, SubAgentType
from agentcli.subagents.planner import PlannerAgent
from agentcli.tools_schema import get_tool_definitions, TOOL_DEFINITIONS


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
    assert d["tool_calls"] == [{"id": "call_123", "type": "function", "function": {"name": "file_ops"}}]
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
                                        "arguments": json.dumps({"operation": "read", "path": "main.py"}),
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
        async def chat_completion(self, messages, model=None, models=None, tools=None, tool_choice=None):
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
                                        "arguments": json.dumps({"operation": "read", "path": "app.py"}),
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
        async def chat_completion(self, messages, model=None, models=None, tools=None, tool_choice=None):
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
