"""Comprehensive tests for Phase 15 Workspace Subagent and Git Grounding."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcli.agent.registry import ToolRegistry
from agentcli.config import Config
from agentcli.session import AgentSession
from agentcli.subagents.base import SubAgentTask, SubAgentType
from agentcli.subagents.planner import PlannerAgent
from agentcli.subagents.workspace import WorkspaceAgent
from agentcli.tools_schema import get_tool_definitions


@pytest.mark.asyncio
async def test_workspace_git_status_mocked(tmp_path: Path) -> None:
    """Test git_status operation on workspace agent."""
    agent = WorkspaceAgent()
    task = SubAgentTask(
        agent_type=SubAgentType.WORKSPACE,
        payload={"operation": "git_status", "path": str(tmp_path)},
    )
    res = await agent.run(task)
    assert res.success is True
    assert "summary" in res.output


@pytest.mark.asyncio
async def test_workspace_search_files(tmp_path: Path) -> None:
    """Test search_files glob matching in workspace agent."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')")
    (tmp_path / "src" / "utils.py").write_text("print('util')")
    (tmp_path / "test_main.py").write_text("assert True")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "ignored.py").write_text("hidden")

    agent = WorkspaceAgent()
    task = SubAgentTask(
        agent_type=SubAgentType.WORKSPACE,
        payload={"operation": "search_files", "path": str(tmp_path), "pattern": "*.py"},
    )
    res = await agent.run(task)
    assert res.success is True
    matches = res.output["matches"]
    assert any("src/main.py" in m or "src\\main.py" in m or "main.py" in m for m in matches)
    assert not any(".git" in m for m in matches)


@pytest.mark.asyncio
async def test_workspace_search_code(tmp_path: Path) -> None:
    """Test search_code content regex search in workspace agent."""
    (tmp_path / "code.py").write_text("def find_my_special_token():\n    return 42\n")

    agent = WorkspaceAgent()
    task = SubAgentTask(
        agent_type=SubAgentType.WORKSPACE,
        payload={
            "operation": "search_code",
            "path": str(tmp_path),
            "query": "find_my_special_token",
        },
    )
    res = await agent.run(task)
    assert res.success is True
    matches = res.output["matches"]
    assert len(matches) == 1
    assert matches[0]["line"] == 1
    assert "find_my_special_token" in matches[0]["content"]


@pytest.mark.asyncio
async def test_workspace_search_code_invalid_regex(tmp_path: Path) -> None:
    """Test search_code error handling on malformed regex."""
    agent = WorkspaceAgent()
    task = SubAgentTask(
        agent_type=SubAgentType.WORKSPACE,
        payload={
            "operation": "search_code",
            "path": str(tmp_path),
            "query": "[unclosed(",
            "is_regex": True,
        },
    )
    res = await agent.run(task)
    assert res.success is False
    assert "Invalid regex" in str(res.error)


@pytest.mark.asyncio
async def test_workspace_list_tree(tmp_path: Path) -> None:
    """Test list_tree directory hierarchy inspection."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.md").write_text("# Doc")

    agent = WorkspaceAgent()
    task = SubAgentTask(
        agent_type=SubAgentType.WORKSPACE,
        payload={"operation": "list_tree", "path": str(tmp_path), "max_depth": 2},
    )
    res = await agent.run(task)
    assert res.success is True
    tree = res.output["tree"]
    assert any("docs" in item for item in tree)


@pytest.mark.asyncio
async def test_workspace_unsupported_operation() -> None:
    """Test unsupported operation error."""
    agent = WorkspaceAgent()
    task = SubAgentTask(
        agent_type=SubAgentType.WORKSPACE,
        payload={"operation": "invalid_op"},
    )
    res = await agent.run(task)
    assert res.success is False
    assert "Unsupported" in str(res.error)


@pytest.mark.asyncio
async def test_tool_registry_workspace_execution(tmp_path: Path) -> None:
    """Test ToolRegistry creates and executes workspace agent."""
    registry = ToolRegistry()
    res = await registry.execute(
        "workspace",
        {"operation": "list_tree", "path": str(tmp_path), "max_depth": 1},
    )
    assert res.success is True


@pytest.mark.asyncio
async def test_auto_ground_workspace(monkeypatch) -> None:
    """Test AgentSession.auto_ground_workspace prepends context."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    config = Config()
    session = AgentSession(config)

    class MockWorkspaceAgent:
        async def run(self, task):
            from agentcli.subagents.base import SubAgentResult

            return SubAgentResult(
                task_id=task.id,
                agent_type=task.agent_type,
                success=True,
                output={"is_git_repo": True, "summary": "Branch: feat/test | Modified: 1"},
            )

    monkeypatch.setattr("agentcli.subagents.workspace.WorkspaceAgent", MockWorkspaceAgent)

    summary = await session.auto_ground_workspace()
    assert summary == "Branch: feat/test | Modified: 1"
    assert session.history[0].content is not None
    assert "[Workspace Context: Branch: feat/test | Modified: 1]" in session.history[0].content


def test_tools_schema_workspace() -> None:
    """Test workspace schema definition in tools_schema."""
    defs = get_tool_definitions([SubAgentType.WORKSPACE])
    assert len(defs) == 1
    assert defs[0]["function"]["name"] == "workspace"
    assert "parameters" in defs[0]["function"]


@pytest.mark.asyncio
async def test_planner_heuristic_workspace() -> None:
    """Test PlannerAgent heuristic creates workspace task."""
    planner = PlannerAgent()
    task = SubAgentTask(
        agent_type=SubAgentType.PLANNER,
        payload={
            "query": "search codebase for MemoryStore",
            "available_agents": [SubAgentType.WORKSPACE, SubAgentType.CODE_ANALYZER],
        },
    )
    res = await planner.run(task)
    assert res.success is True
    plan = res.output["plan"]
    assert any(step["agent_type"] == "workspace" for step in plan)


@pytest.mark.asyncio
async def test_workspace_git_branch_and_worktree_validation(tmp_path: Path) -> None:
    """Test parameter validation for git_branch and git_worktree."""
    agent = WorkspaceAgent()

    # Empty branch name
    res_b = await agent.run(
        SubAgentTask(
            agent_type=SubAgentType.WORKSPACE,
            payload={"operation": "git_branch", "path": str(tmp_path)},
        )
    )
    assert res_b.success is False
    assert "No branch_name" in str(res_b.error)

    # Empty worktree path
    res_w = await agent.run(
        SubAgentTask(
            agent_type=SubAgentType.WORKSPACE,
            payload={"operation": "git_worktree", "path": str(tmp_path)},
        )
    )
    assert res_w.success is False
    assert "No worktree_path" in str(res_w.error)

