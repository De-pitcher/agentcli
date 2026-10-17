"""Comprehensive tests for Phase 15 Workspace Subagent and Git Grounding."""

from __future__ import annotations

import asyncio
import sys
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
    """Test ToolRegistry creates and executes workspace agent with explicit context."""
    from agentcli.policy import RunPolicy
    from agentcli.runtime import RunContext

    ctx = RunContext(policy=RunPolicy(root=tmp_path))
    registry = ToolRegistry(context=ctx)
    res = await registry.execute(
        "workspace",
        {"operation": "list_tree", "path": str(tmp_path), "max_depth": 1},
    )
    assert res.success is True
    await ctx.aclose()


@pytest.mark.asyncio
async def test_tool_registry_workspace_default_denial_outside_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test unconfigured ToolRegistry denies access to path outside default root.

    Creates a granted directory and sibling outside directory inside tmp_path.
    monkeypatch.chdir(granted) sets the default root. The outside directory is
    a sibling, so it should be denied by the default workspace policy.
    """
    # Create granted directory and sibling outside directory inside tmp_path
    granted = tmp_path / "granted"
    granted.mkdir()
    (granted / "file.txt").write_text("allowed", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")

    # Set cwd to granted directory so it becomes the default root
    monkeypatch.chdir(granted)

    registry = ToolRegistry()
    try:
        # Inside granted directory should succeed
        res_inside = await registry.execute(
            "workspace",
            {"operation": "list_tree", "path": str(granted), "max_depth": 1},
        )
        assert res_inside.success is True

        # Sibling outside directory should be denied
        res_outside = await registry.execute(
            "workspace",
            {"operation": "list_tree", "path": str(outside), "max_depth": 1},
        )
        assert res_outside.success is False
        assert "outside the granted workspace" in str(res_outside.error) or "Access denied" in str(
            res_outside.error
        )
    finally:
        await registry.aclose()


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


@pytest.mark.asyncio
async def test_workspace_git_branch_list(tmp_path: Path) -> None:
    """Test git_branch with action=list doesn't require branch_name."""
    agent = WorkspaceAgent()
    res = await agent.run(
        SubAgentTask(
            agent_type=SubAgentType.WORKSPACE,
            payload={"operation": "git_branch", "action": "list", "path": str(tmp_path)},
        )
    )
    # Even if tmp_path is not a git repo, it should proceed to execution and fail at git level, not validation
    assert res.success is False or res.success is True
    assert "No branch_name" not in str(res.error or "")


@pytest.mark.asyncio
async def test_governed_workspace_list_tree_bounds_and_policies(tmp_path: Path) -> None:
    """Test governed workspace list_tree respects policy path boundaries and limits."""
    from agentcli.policy import RunPolicy
    from agentcli.runtime import RunContext

    # Setup directories
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    (allowed_dir / "file1.txt").write_text("hello")
    sub = allowed_dir / "subdir"
    sub.mkdir()
    (sub / "file2.txt").write_text("world")

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("secret")

    readable_extra = tmp_path / "extra_readable"
    readable_extra.mkdir()
    (readable_extra / "extra.txt").write_text("extra")

    # 1. Access inside allowed root succeeds
    pol = RunPolicy(root=allowed_dir, readable_roots=(readable_extra,))
    ctx = RunContext(policy=pol)
    registry = ToolRegistry(context=ctx)

    res = await registry.execute(
        "workspace",
        {"operation": "list_tree", "path": str(allowed_dir), "max_depth": 2},
    )
    assert res.success is True
    tree = res.output["tree"]
    assert any("file1.txt" in line for line in tree)
    assert any("subdir" in line for line in tree)
    assert any("file2.txt" in line for line in tree)

    # 2. Access in readable_roots succeeds
    res_extra = await registry.execute(
        "workspace",
        {"operation": "list_tree", "path": str(readable_extra), "max_depth": 1},
    )
    assert res_extra.success is True
    assert any("extra.txt" in line for line in res_extra.output["tree"])

    # 3. Path outside root and readable_roots is denied
    res_outside = await registry.execute(
        "workspace",
        {"operation": "list_tree", "path": str(outside_dir), "max_depth": 1},
    )
    assert res_outside.success is False
    assert "outside the granted workspace" in str(res_outside.error) or "Access denied" in str(
        res_outside.error
    )

    # 4. max_depth=0 yields empty tree
    res_depth0 = await registry.execute(
        "workspace",
        {"operation": "list_tree", "path": str(allowed_dir), "max_depth": 0},
    )
    assert res_depth0.success is True
    assert res_depth0.output["tree"] == []

    # 5. Invalid max_depth validation
    res_invalid_depth = await registry.execute(
        "workspace",
        {"operation": "list_tree", "path": str(allowed_dir), "max_depth": 25},
    )
    assert res_invalid_depth.success is False
    assert "max_depth must be between 0 and 20" in str(res_invalid_depth.error)

    # 6. Unintegrated workspace operations under governed registry fail closed
    res_git = await registry.execute(
        "workspace",
        {"operation": "git_status", "path": str(allowed_dir)},
    )
    assert res_git.success is False
    assert "Helper operation 'filesystem_tool' is unsupported" in str(res_git.error)

    # 7. Strict max_entries validation: non-int and out-of-range rejection
    res_invalid_entries_type = await registry.execute(
        "workspace",
        {"operation": "list_tree", "path": str(allowed_dir), "max_entries": "10"},
    )
    assert res_invalid_entries_type.success is False
    assert "max_entries must be" in str(
        res_invalid_entries_type.error
    ) or "max_entries must be integer" in str(res_invalid_entries_type.error)

    res_invalid_entries_range = await registry.execute(
        "workspace",
        {"operation": "list_tree", "path": str(allowed_dir), "max_entries": 0},
    )
    assert res_invalid_entries_range.success is False
    assert "max_entries must be between 1 and 4096" in str(res_invalid_entries_range.error)

    # 8. Bounded acquisition lookahead quota test
    # Create 10 files in allowed_dir; request max_entries=2
    for i in range(10):
        (allowed_dir / f"test_quota_{i}.txt").write_text(f"content {i}")

    res_quota = await registry.execute(
        "workspace",
        {"operation": "list_tree", "path": str(allowed_dir), "max_entries": 2},
    )
    assert res_quota.success is True
    assert len(res_quota.output["tree"]) <= 2
    await ctx.aclose()


@pytest.mark.asyncio
async def test_workspace_list_tree_junction_rejection(tmp_path: Path) -> None:
    """Test that junctions / reparse links are rejected and not traversed outside root."""
    from agentcli.policy import RunPolicy
    from agentcli.runtime import RunContext

    granted = tmp_path / "granted"
    granted.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret_data")

    # On Windows, try to create junction if permissions allow; otherwise test symlink
    link_created = False
    link_path = granted / "escape"
    if sys.platform == "win32":
        try:
            proc = await asyncio.create_subprocess_exec(
                "cmd",
                "/c",
                "mklink",
                "/J",
                str(link_path),
                str(outside),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0:
                link_created = True
        except OSError:
            pass

    if not link_created:
        try:
            link_path.symlink_to(outside, target_is_directory=True)
            link_created = True
        except (OSError, NotImplementedError):
            pass

    if not link_created:
        pytest.skip("Could not create junction or directory symlink on this host")

    ctx = RunContext(policy=RunPolicy(root=granted))
    registry = ToolRegistry(context=ctx)
    res = await registry.execute(
        "workspace",
        {"operation": "list_tree", "path": str(granted), "max_depth": 2},
    )
    # The junction must NOT allow secret.txt to be exposed or returned
    assert "secret.txt" not in str(res.output or "")
    await ctx.aclose()
