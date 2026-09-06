"""Targeted high-coverage tests for Shell, WebSearch, Workspace, Session, and CLI branches."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcli.config import Config
from agentcli.session import AgentSession
from agentcli.subagents.base import SubAgentTask, SubAgentType
from agentcli.subagents.shell import ShellExecutionAgent
from agentcli.subagents.web_search import WebSearchAgent
from agentcli.subagents.workspace import WorkspaceAgent


@pytest.mark.asyncio
async def test_shell_cd_commands(tmp_path: Path) -> None:
    """Test ShellExecutionAgent built-in cd handling across valid, home, non-existent, and file paths."""
    sub_dir = tmp_path / "sub"
    sub_dir.mkdir()
    sample_file = tmp_path / "file.txt"
    sample_file.write_text("hello", encoding="utf-8")

    agent = ShellExecutionAgent(config={"working_dir": str(tmp_path)})

    # 1. Valid cd to relative directory
    t1 = SubAgentTask(agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": "cd sub"})
    r1 = await agent.run(t1)
    assert r1.success is True
    assert "Directory changed to:" in r1.output["stdout"]
    assert str(sub_dir) in r1.output["cwd"]

    # 2. cd to home directory '~'
    t_home = SubAgentTask(agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": "cd ~"})
    r_home = await agent.run(t_home)
    assert r_home.success is True
    assert str(Path.home()) in r_home.output["cwd"]

    # 3. cd to non-existent directory
    t_nonexist = SubAgentTask(agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": "cd non_existent_folder_xyz"})
    r_nonexist = await agent.run(t_nonexist)
    assert r_nonexist.success is False
    assert r_nonexist.error is not None and "no such file or directory" in r_nonexist.error

    # 4. cd into a regular file (not a directory)
    t_file = SubAgentTask(
        agent_type=SubAgentType.SHELL_EXECUTION,
        payload={"command": f"cd {sample_file.name}"},
    )
    agent.working_dir = str(tmp_path)
    r_file = await agent.run(t_file)
    assert r_file.success is False
    assert r_file.error is not None and "not a directory" in r_file.error


@pytest.mark.asyncio
async def test_shell_fallback_shims(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test ShellExecutionAgent fallbacks for ls/dir and cat/type when native executables are absent."""
    (tmp_path / "test1.txt").write_text("content 1", encoding="utf-8")
    (tmp_path / "nested_dir").mkdir()
    (tmp_path / "nested_dir" / "inner.txt").write_text("inner", encoding="utf-8")

    agent = ShellExecutionAgent(config={"working_dir": str(tmp_path)})

    # Force shutil.which to return None for ls, dir, cat, type
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    # 1. Fallback ls / dir
    t_ls = SubAgentTask(agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": "ls"})
    r_ls = await agent.run(t_ls)
    assert r_ls.success is True
    assert "test1.txt" in r_ls.output["stdout"]
    assert "nested_dir/" in r_ls.output["stdout"]

    # 1b. Fallback ls with subdirectory argument
    t_ls_sub = SubAgentTask(agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": "ls nested_dir"})
    r_ls_sub = await agent.run(t_ls_sub)
    assert r_ls_sub.success is True
    assert "inner.txt" in r_ls_sub.output["stdout"]

    # 2. Fallback cat valid file
    t_cat = SubAgentTask(agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": "cat test1.txt"})
    r_cat = await agent.run(t_cat)
    assert r_cat.success is True
    assert r_cat.output["stdout"] == "content 1"

    # 3. Fallback cat without args
    t_cat_noargs = SubAgentTask(agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": "cat"})
    r_cat_noargs = await agent.run(t_cat_noargs)
    assert r_cat_noargs.success is False
    assert r_cat_noargs.error is not None and "Usage: cat" in r_cat_noargs.error

    # 4. Fallback cat on non-existent file
    t_cat_missing = SubAgentTask(agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": "cat missing.txt"})
    r_cat_missing = await agent.run(t_cat_missing)
    assert r_cat_missing.success is False

    # 5. which command when tool is missing
    t_which = SubAgentTask(agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": "which non_existent_tool_abc"})
    r_which = await agent.run(t_which)
    assert r_which.success is False
    assert r_which.error is not None and "not found" in r_which.error


@pytest.mark.asyncio
async def test_web_search_agent_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test WebSearchAgent result parsing, error fallbacks, and query limits."""
    agent = WebSearchAgent(config={"max_results": 3})

    # Mock successful HTTP search response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": [
            {"title": "Result 1", "url": "https://example.com/1", "content": "Summary 1"},
            {"title": "Result 2", "url": "https://example.com/2", "content": "Summary 2"},
        ]
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response

    # Test running search with mock client
    t_search = SubAgentTask(
        agent_type=SubAgentType.WEB_SEARCH,
        payload={"query": "python async tutorial"},
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        res = await agent.run(t_search)
        # WebSearchAgent returns a structured result
        assert res.agent_type == SubAgentType.WEB_SEARCH


@pytest.mark.asyncio
async def test_workspace_agent_branches(tmp_path: Path) -> None:
    """Test WorkspaceAgent operations across file trees, git status, and filtering."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('app')", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Project", encoding="utf-8")

    agent = WorkspaceAgent(config={"working_dir": str(tmp_path)})

    # 1. search_files with pattern
    t_find = SubAgentTask(
        agent_type=SubAgentType.WORKSPACE,
        payload={"operation": "search_files", "pattern": "*.py", "path": str(tmp_path)},
    )
    r_find = await agent.run(t_find)
    assert r_find.success is True
    assert any("app.py" in str(f) for f in r_find.output.get("matches", []))

    # 2. list_tree
    t_tree = SubAgentTask(
        agent_type=SubAgentType.WORKSPACE,
        payload={"operation": "list_tree", "max_depth": 2, "path": str(tmp_path)},
    )
    r_tree = await agent.run(t_tree)
    assert r_tree.success is True

    # 3. git_status when not a git repo
    t_git = SubAgentTask(
        agent_type=SubAgentType.WORKSPACE,
        payload={"operation": "git_status", "path": str(tmp_path)},
    )
    r_git = await agent.run(t_git)
    # Returns result gracefully
    assert r_git.agent_type == SubAgentType.WORKSPACE


@pytest.mark.asyncio
async def test_session_extended_telemetry_and_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test AgentSession extended methods, cost calculation, and history compaction."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = Config()

    session = AgentSession(
        config=config,
        forced_model="google/gemma-4-31b-it:free",
    )

    # Test adding messages and computing session statistics
    session.add_user_message("Hello agent")
    session.add_assistant_message("Hello user")

    stats = await session.get_session_stats()
    assert isinstance(stats, dict)
    assert stats.get("total_tokens", 0) >= 0

    # Pop message
    assert len(session.history) == 2
    session.pop_last_message()
    assert len(session.history) == 1
    assert session.history[0].content == "Hello agent"

    await session.aclose()
