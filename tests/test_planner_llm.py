"""Tests for LLM-based planner functionality."""

from __future__ import annotations

import pytest

from agentcli.subagents.base import SubAgentTask, SubAgentType
from agentcli.subagents.planner import PlannerAgent


@pytest.mark.asyncio
async def test_planner_llm_valid_json_response(monkeypatch) -> None:
    """Test LLM-based planning with valid JSON response."""
    from agentcli.config import Config

    agent = PlannerAgent()
    cfg = Config()
    agent._set_config(cfg)

    class MockClient:
        async def chat_stream(self, messages, model=None, models=None):
            yield '[{"agent_type": "code_analyzer", "payload": {"files": ["test.py"], "focus": "security", "context": "test"}, "priority": 10, "goal_criterion": "security"}]'

    monkeypatch.setattr("agentcli.subagents.planner.OpenRouterClient", lambda _: MockClient())

    task = SubAgentTask(
        agent_type=SubAgentType.PLANNER,
        payload={
            "query": "analyze security in test.py",
            "model": "test-model",
        },
    )
    result = await agent.run(task)
    assert result.success is True
    plan = result.output["plan"]
    assert len(plan) == 1
    assert plan[0]["agent_type"] == "code_analyzer"
    assert plan[0]["goal_criterion"] == "security"


@pytest.mark.asyncio
async def test_planner_llm_invalid_json_fallback(monkeypatch) -> None:
    """Test fallback to heuristic when LLM returns invalid JSON."""
    from agentcli.config import Config

    agent = PlannerAgent()
    cfg = Config()
    agent._set_config(cfg)

    class MockClient:
        async def chat_stream(self, messages, model=None, models=None):
            yield "this is not valid json"

    monkeypatch.setattr("agentcli.subagents.planner.OpenRouterClient", lambda _: MockClient())

    task = SubAgentTask(
        agent_type=SubAgentType.PLANNER,
        payload={
            "query": "analyze bug in test.py",
            "model": "test-model",
        },
    )
    result = await agent.run(task)
    assert result.success is True
    plan = result.output["plan"]
    assert len(plan) > 0
    assert plan[0]["agent_type"] == "code_analyzer"


@pytest.mark.asyncio
async def test_planner_llm_non_list_json_fallback(monkeypatch) -> None:
    """Test fallback when LLM returns non-list JSON."""
    from agentcli.config import Config

    agent = PlannerAgent()
    cfg = Config()
    agent._set_config(cfg)

    class MockClient:
        async def chat_stream(self, messages, model=None, models=None):
            yield '{"not": "a list"}'

    monkeypatch.setattr("agentcli.subagents.planner.OpenRouterClient", lambda _: MockClient())

    task = SubAgentTask(
        agent_type=SubAgentType.PLANNER,
        payload={
            "query": "analyze bug in test.py",
            "model": "test-model",
        },
    )
    result = await agent.run(task)
    assert result.success is True
    plan = result.output["plan"]
    assert len(plan) > 0


@pytest.mark.asyncio
async def test_planner_llm_openrouter_error_fallback(monkeypatch) -> None:
    """Test fallback when OpenRouterError occurs."""
    from agentcli.config import Config
    from agentcli.openrouter_client import OpenRouterError

    agent = PlannerAgent()
    cfg = Config()
    agent._set_config(cfg)

    class MockClient:
        async def chat_stream(self, messages, model=None, models=None):
            raise OpenRouterError("API error")
            yield  # Make this an async generator

    monkeypatch.setattr("agentcli.subagents.planner.OpenRouterClient", lambda _: MockClient())

    task = SubAgentTask(
        agent_type=SubAgentType.PLANNER,
        payload={
            "query": "analyze bug in test.py",
            "model": "test-model",
        },
    )
    result = await agent.run(task)
    assert result.success is True
    plan = result.output["plan"]
    assert len(plan) > 0


@pytest.mark.asyncio
async def test_planner_llm_config_not_set_fallback() -> None:
    """Test fallback when config is not set for LLM planning."""
    agent = PlannerAgent()
    # Don't call _set_config

    task = SubAgentTask(
        agent_type=SubAgentType.PLANNER,
        payload={
            "query": "analyze bug in test.py",
            "model": "test-model",
        },
    )
    result = await agent.run(task)
    assert result.success is True
    plan = result.output["plan"]
    assert len(plan) > 0


@pytest.mark.asyncio
async def test_planner_llm_validation_against_allowed_types(monkeypatch) -> None:
    """Test that LLM response is validated against allowed agent types."""
    from agentcli.config import Config

    agent = PlannerAgent()
    cfg = Config()
    agent._set_config(cfg)

    class MockClient:
        async def chat_stream(self, messages, model=None, models=None):
            yield '[{"agent_type": "web_search", "payload": {"query": "test"}, "priority": 1, "goal_criterion": "test"}]'

    monkeypatch.setattr("agentcli.subagents.planner.OpenRouterClient", lambda _: MockClient())

    task = SubAgentTask(
        agent_type=SubAgentType.PLANNER,
        payload={
            "query": "search for something",
            "model": "test-model",
            "available_agents": [SubAgentType.CODE_ANALYZER, SubAgentType.FILE_OPS],
        },
    )
    result = await agent.run(task)
    assert result.success is True
    plan = result.output["plan"]
    assert len(plan) == 1
    assert plan[0]["agent_type"] == "code_analyzer"


@pytest.mark.asyncio
async def test_planner_llm_no_valid_tasks_fallback(monkeypatch) -> None:
    """Test fallback when LLM produces no valid tasks after filtering."""
    from agentcli.config import Config

    agent = PlannerAgent()
    cfg = Config()
    agent._set_config(cfg)

    class MockClient:
        async def chat_stream(self, messages, model=None, models=None):
            yield "[]"

    monkeypatch.setattr("agentcli.subagents.planner.OpenRouterClient", lambda _: MockClient())

    task = SubAgentTask(
        agent_type=SubAgentType.PLANNER,
        payload={
            "query": "analyze bug in test.py",
            "model": "test-model",
        },
    )
    result = await agent.run(task)
    assert result.success is True
    plan = result.output["plan"]
    assert len(plan) > 0
    assert plan[0]["agent_type"] == "code_analyzer"


@pytest.mark.asyncio
async def test_planner_llm_multiple_steps(monkeypatch) -> None:
    """Test LLM-based planning with multiple steps."""
    from agentcli.config import Config

    agent = PlannerAgent()
    cfg = Config()
    agent._set_config(cfg)

    class MockClient:
        async def chat_stream(self, messages, model=None, models=None):
            yield """[
                {"agent_type": "file_ops", "payload": {"operation": "read", "path": "README.md"}, "priority": 5, "goal_criterion": "README"},
                {"agent_type": "code_analyzer", "payload": {"files": ["README.md"], "focus": "general", "context": "User wants analysis"}, "priority": 10, "goal_criterion": "analysis"}
            ]"""

    monkeypatch.setattr("agentcli.subagents.planner.OpenRouterClient", lambda _: MockClient())

    task = SubAgentTask(
        agent_type=SubAgentType.PLANNER,
        payload={
            "query": "read README.md and analyze it",
            "model": "test-model",
        },
    )
    result = await agent.run(task)
    assert result.success is True
    plan = result.output["plan"]
    assert len(plan) == 2
    assert plan[0]["agent_type"] == "file_ops"
    assert plan[1]["agent_type"] == "code_analyzer"
    assert plan[0]["goal_criterion"] == "README"
    assert plan[1]["goal_criterion"] == "analysis"
