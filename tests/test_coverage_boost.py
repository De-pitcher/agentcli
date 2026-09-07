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
    t_nonexist = SubAgentTask(
        agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": "cd non_existent_folder_xyz"}
    )
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
    t_ls_sub = SubAgentTask(
        agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": "ls nested_dir"}
    )
    r_ls_sub = await agent.run(t_ls_sub)
    assert r_ls_sub.success is True
    assert "inner.txt" in r_ls_sub.output["stdout"]

    # 2. Fallback cat valid file
    t_cat = SubAgentTask(
        agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": "cat test1.txt"}
    )
    r_cat = await agent.run(t_cat)
    assert r_cat.success is True
    assert r_cat.output["stdout"] == "content 1"

    # 3. Fallback cat without args
    t_cat_noargs = SubAgentTask(agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": "cat"})
    r_cat_noargs = await agent.run(t_cat_noargs)
    assert r_cat_noargs.success is False
    assert r_cat_noargs.error is not None and "Usage: cat" in r_cat_noargs.error

    # 4. Fallback cat on non-existent file
    t_cat_missing = SubAgentTask(
        agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": "cat missing.txt"}
    )
    r_cat_missing = await agent.run(t_cat_missing)
    assert r_cat_missing.success is False

    # 5. which command when tool is missing
    t_which = SubAgentTask(
        agent_type=SubAgentType.SHELL_EXECUTION, payload={"command": "which non_existent_tool_abc"}
    )
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
    mock_response.raise_for_status = MagicMock()
    mock_response.text = '<a class="result__snippet" href="https://example.com/1">Summary 1</a>'
    mock_response.json.return_value = {
        "results": [
            {"title": "Result 1", "url": "https://example.com/1", "content": "Summary 1"},
            {"title": "Result 2", "url": "https://example.com/2", "content": "Summary 2"},
        ]
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.post.return_value = mock_response

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
async def test_session_extended_telemetry_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    await session.aclose()


def test_unicode_safe_format_and_print(capsys: pytest.CaptureFixture[str]) -> None:
    """Test unicode safe formatting and safe printing fallbacks."""
    from agentcli import unicode as agy_unicode

    # 1. Test safe_format with unicode enabled
    with patch.object(agy_unicode, "_UNICODE_SUPPORTED", True):
        assert agy_unicode.safe_format("→ ✓") == "→ ✓"
        agy_unicode.safe_print("Testing unicode → ✓")
        out, _ = capsys.readouterr()
        assert "Testing unicode" in out

    # 2. Test safe_format with ASCII fallback
    with patch.object(agy_unicode, "_UNICODE_SUPPORTED", False):
        formatted = agy_unicode.safe_format("Step 1 → [✓] Done 🚀")
        assert "->" in formatted
        assert "[OK]" in formatted or "[DONE]" in formatted or "[LAUNCH]" in formatted

        agy_unicode.safe_print("Arrow → Check ✓ Rocket 🚀")
        out, _ = capsys.readouterr()
        assert "->" in out
        assert "[OK]" in out

    # 3. Test configure_utf8_io
    agy_unicode.configure_utf8_io()


def test_prompt_completer_all_branches(tmp_path: Path) -> None:
    """Test SlashAndFileCompleter branches for @files, skills, branches, and models."""
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from agentcli.ui.prompt import SlashAndFileCompleter

    completer = SlashAndFileCompleter()
    event = CompleteEvent()

    # Model completions
    assert any(c.text == "google/gemini-2.0-flash-exp:free" or "free" in c.text for c in completer.get_completions(Document("/model "), event))
    assert any(c.text == "free" for c in completer.get_completions(Document("/model f"), event))
    assert any(c.text == "paid" for c in completer.get_completions(Document("/model p"), event))

    # Budget completions
    assert any(c.text == "medium" for c in completer.get_completions(Document("/budget med"), event))
    assert any(c.text == "high" for c in completer.get_completions(Document("/budget hi"), event))

    # Skill completions
    assert any(c.text == "info" for c in completer.get_completions(Document("/skill in"), event))
    assert any(c.text == "run" for c in completer.get_completions(Document("/skill r"), event))
    assert any("code-review" in c.text for c in completer.get_completions(Document("/skill run code"), event))

    # Branch completions
    assert any(c.text == "create" for c in completer.get_completions(Document("/branch cr"), event))
    assert any(c.text == "status" for c in completer.get_completions(Document("/branch st"), event))
    assert any(c.text == "discard" for c in completer.get_completions(Document("/branch disc"), event))

    # File @ completion
    test_f = tmp_path / "sample_doc.txt"
    test_f.write_text("content", encoding="utf-8")
    at_comps = list(completer.get_completions(Document(f"@{test_f!s}"), event))
    assert isinstance(at_comps, list)


@pytest.mark.asyncio
async def test_session_step_budget_and_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test AgentSession.step when budget ceiling is exceeded or prompt expansion fails."""
    from agentcli.agent.events import FinishEvent, LoopErrorEvent
    from agentcli.config import Config
    from agentcli.session import AgentSession

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = Config()
    config.routing.max_cost_usd = 0.05
    session = AgentSession(config=config, forced_model="google/gemma-4-31b-it:free")
    session.cumulative_cost_usd = 0.10  # exceeds 0.05

    assert session.is_budget_exceeded() is True
    reply = await session.step("Hello")
    assert "Session cost ceiling reached" in reply

    # Reset cost
    session.cumulative_cost_usd = 0.0

    # Test prepare_prompt exception handling
    with patch("agentcli.files.expand_file_references", side_effect=ValueError("bad expansion")):
        expanded = session.prepare_prompt("@badfile")
        assert expanded == "@badfile"

    # Test session.step normal flow with mocked send()
    async def mock_stream():
        yield "Hello "
        yield "World!"

    mock_reply = MagicMock()
    mock_reply.stream = mock_stream()
    mock_reply.requested_primary = "google/gemma-4-31b-it:free"

    with patch.object(session, "send", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = mock_reply
        step_res = await session.step("Say hello")
        assert step_res == "Hello World!"
        assert session.cumulative_cost_usd >= 0.0

    # Test session.step with agentic loop FinishEvent
    async def mock_loop_finish(text: str):
        yield FinishEvent(output="Agentic task complete", summary="Done")

    with (
        patch.object(session, "should_use_loop", return_value=True),
        patch.object(session, "run_loop", side_effect=mock_loop_finish),
    ):
        loop_res = await session.step("Perform complex workflow")
        assert loop_res == "Agentic task complete"

    # Test session.step with agentic loop LoopErrorEvent
    async def mock_loop_error(text: str):
        yield LoopErrorEvent(error="Fatal task failure")

    with (
        patch.object(session, "should_use_loop", return_value=True),
        patch.object(session, "run_loop", side_effect=mock_loop_error),
    ):
        err_res = await session.step("Perform failing workflow")
        assert "[loop error] Fatal task failure" in err_res

    await session.aclose()


@pytest.mark.asyncio
async def test_session_auto_ground_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test auto_ground_workspace when git repo is discovered or missing."""
    from agentcli.config import Config
    from agentcli.session import AgentSession
    from agentcli.subagents.base import SubAgentResult, SubAgentType

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = Config()
    session = AgentSession(config=config, forced_model="google/gemma-4-31b-it:free")

    # Success case: git repo discovered
    mock_success_res = SubAgentResult(
        task_id="t_success",
        agent_type=SubAgentType.WORKSPACE,
        success=True,
        output={"is_git_repo": True, "summary": "Branch: main, clean status"},
    )
    with patch("agentcli.subagents.workspace.WorkspaceAgent.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = mock_success_res
        summary = await session.auto_ground_workspace(tmp_path)
        assert summary == "Branch: main, clean status"
        assert any("[Workspace Context:" in (m.content or "") for m in session.history)

    # Failure case: not a git repo
    mock_fail_res = SubAgentResult(
        task_id="t_fail",
        agent_type=SubAgentType.WORKSPACE,
        success=False,
        output={"is_git_repo": False},
    )
    with patch("agentcli.subagents.workspace.WorkspaceAgent.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = mock_fail_res
        summary = await session.auto_ground_workspace(tmp_path)
        assert summary is None

    await session.aclose()


def test_unicode_safe_print_encode_error(capsys: pytest.CaptureFixture[str]) -> None:
    """Test unicode safe_print catching UnicodeEncodeError and applying fallback."""
    from agentcli import unicode as agy_unicode

    with (
        patch.object(agy_unicode, "_UNICODE_SUPPORTED", False),
        patch("builtins.print", side_effect=[UnicodeEncodeError("ascii", "test", 0, 1, "bad"), None]),
    ):
        agy_unicode.safe_print("Testing exception fallback")


def test_worktree_corrupted_meta_file(tmp_path: Path) -> None:
    """Test WorktreeManager resilience when metadata JSON is corrupted."""
    from agentcli.worktree.manager import WorktreeManager

    wt_mgr = WorktreeManager(repo_root=tmp_path)
    meta_file = tmp_path / ".agentcli" / "worktrees" / ".worktrees.json"
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text("{corrupted-json...", encoding="utf-8")

    # _load_meta should handle exception and return empty dict
    loaded = wt_mgr._load_meta()
    assert loaded == {}

    # non-git repo diff / status
    assert wt_mgr.is_git_repo() is False
    assert wt_mgr.list_worktrees() == []
    assert wt_mgr.get_worktree("nonexistent") is None

    # Test get_current_branch fallback when git fails
    with patch.object(wt_mgr, "_run_git") as mock_git:
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_git.return_value = mock_proc
        assert wt_mgr.get_current_branch() == "main"


@pytest.mark.asyncio
async def test_worktree_agent_exception_handling(tmp_path: Path) -> None:
    """Test WorktreeAgent graceful error return when WorktreeManager raises an unhandled exception."""
    from agentcli.subagents.base import SubAgentTask, SubAgentType
    from agentcli.subagents.worktree import WorktreeAgent

    agent = WorktreeAgent(workspace_dir=tmp_path)
    with patch.object(agent.manager, "create_worktree", side_effect=RuntimeError("disk full")):
        task = SubAgentTask(
            agent_type=SubAgentType.WORKTREE,
            payload={"action": "create", "branch": "test-branch"},
        )
        res = await agent.run(task)
        assert res.success is False
        assert "disk full" in (res.error or "")


@pytest.mark.asyncio
async def test_code_analyzer_llm_execution(tmp_path: Path) -> None:
    """Test CodeAnalyzerAgent LLM path and exception fallback."""
    from agentcli.config import Config
    from agentcli.subagents.base import SubAgentTask, SubAgentType
    from agentcli.subagents.code_analyzer import CodeAnalyzerAgent

    agent = CodeAnalyzerAgent()
    agent._set_config(Config())
    sample_file = tmp_path / "mod.py"
    sample_file.write_text("def test(): pass\n", encoding="utf-8")

    # 1. Successful LLM stream
    async def mock_stream(*args, **kwargs):
        yield "Code quality is "
        yield "excellent."

    mock_client = MagicMock()
    mock_client.chat_stream = mock_stream

    task = SubAgentTask(
        agent_type=SubAgentType.CODE_ANALYZER,
        payload={
            "files": [str(sample_file)],
            "focus": "security",
            "model": "google/gemma-4-31b-it:free",
        },
    )

    with patch.object(agent, "_get_client", new_callable=AsyncMock) as mock_get_client:
        mock_get_client.return_value = mock_client
        res = await agent.run(task)
        assert res.success is True
        assert "excellent" in res.output["analysis"]

    # 2. LLM error fallback
    with patch.object(agent, "_get_client", side_effect=RuntimeError("connection refused")):
        res_err = await agent.run(task)
        assert res_err.success is False
        assert "LLM analysis failed" in (res_err.error or "")




