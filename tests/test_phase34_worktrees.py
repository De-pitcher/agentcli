"""Tests for Phase 34: Git Worktrees & Branch Sandboxing."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentcli.agent.registry import ToolRegistry
from agentcli.subagents.base import SubAgentTask, SubAgentType
from agentcli.subagents.worktree import WorktreeAgent
from agentcli.tools_schema import TOOL_DEFINITIONS, get_tool_definitions
from agentcli.ui.prompt import resolve_slash_command
from agentcli.worktree.manager import WorktreeManager, WorktreeMetadata


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Initialize a mock git repo with an initial commit."""
    repo = tmp_path / "test_repo"
    repo.mkdir()

    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Agent"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@agentcli.ai"], cwd=str(repo), check=True, capture_output=True)

    # Initial file and commit
    init_file = repo / "README.md"
    init_file.write_text("# Initial Repo\nHello world\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(repo), check=True, capture_output=True)

    return repo


# ---------------------------------------------------------------------------
# 1. Worktree Metadata Tests
# ---------------------------------------------------------------------------


def test_worktree_metadata_serialization():
    meta = WorktreeMetadata(
        id="wt_123",
        branch="feat/test-branch",
        path="/tmp/wt",
        base_ref="main",
        status="active",
        commit_sha="abcdef123456",
    )
    d = meta.to_dict()
    assert d["id"] == "wt_123"
    assert d["branch"] == "feat/test-branch"
    assert d["status"] == "active"
    assert d["commit_sha"] == "abcdef123456"


# ---------------------------------------------------------------------------
# 2. Worktree Manager Lifecycle Tests
# ---------------------------------------------------------------------------


def test_worktree_manager_create_and_list(git_repo: Path):
    manager = WorktreeManager(repo_root=git_repo)
    assert manager.is_git_repo() is True
    assert manager.get_current_branch() == "main"

    # Create worktree
    wt = manager.create_worktree("feat/sandbox-1")
    assert wt.branch == "feat/sandbox-1"
    assert Path(wt.path).exists()
    assert (Path(wt.path) / "README.md").exists()

    # List worktrees
    all_wts = manager.list_worktrees()
    assert len(all_wts) == 1
    assert all_wts[0].branch == "feat/sandbox-1"

    # Get worktree lookup
    found = manager.get_worktree("feat/sandbox-1")
    assert found is not None
    assert found.path == wt.path


def test_worktree_manager_diff_and_dirty_status(git_repo: Path):
    manager = WorktreeManager(repo_root=git_repo)
    wt = manager.create_worktree("feat/sandbox-edit")

    # Initial status -> clean
    status = manager.get_worktree_status("feat/sandbox-edit")
    assert status["dirty"] is False
    assert status["changes_count"] == 0

    # Modify a file inside worktree
    mod_file = Path(wt.path) / "feature.py"
    mod_file.write_text("print('hello sandbox')\n", encoding="utf-8")

    # Status -> dirty
    dirty_status = manager.get_worktree_status("feat/sandbox-edit")
    assert dirty_status["dirty"] is True
    assert dirty_status["changes_count"] >= 1

    # Commit inside worktree and compute diff
    subprocess.run(["git", "add", "feature.py"], cwd=wt.path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add feature.py"], cwd=wt.path, check=True, capture_output=True)

    diff_text = manager.compute_diff("feat/sandbox-edit")
    assert "feature.py" in diff_text
    assert "hello sandbox" in diff_text


def test_worktree_manager_merge_and_remove(git_repo: Path):
    manager = WorktreeManager(repo_root=git_repo)
    wt = manager.create_worktree("feat/sandbox-merge")

    # Add change in worktree
    new_doc = Path(wt.path) / "DOCS.md"
    new_doc.write_text("# Sandbox Documentation\n", encoding="utf-8")

    # Merge worktree into main
    merge_res = manager.merge_worktree("feat/sandbox-merge", strategy="squash")
    assert merge_res["success"] is True

    # Main repo should now have DOCS.md
    assert (git_repo / "DOCS.md").exists()

    # Remove worktree
    removed = manager.remove_worktree("feat/sandbox-merge", force=True, delete_branch=True)
    assert removed is True
    assert not Path(wt.path).exists()
    assert len(manager.list_worktrees()) == 0


def test_worktree_manager_prune_all(git_repo: Path):
    manager = WorktreeManager(repo_root=git_repo)
    pruned = manager.prune_all()
    assert isinstance(pruned, int)


def test_worktree_manager_non_git_repo(tmp_path: Path):
    empty_dir = tmp_path / "not_a_repo"
    empty_dir.mkdir()
    manager = WorktreeManager(repo_root=empty_dir)
    assert manager.is_git_repo() is False
    assert manager.list_worktrees() == []

    with pytest.raises(RuntimeError, match="not a valid Git repository"):
        manager.create_worktree("feat/invalid")


# ---------------------------------------------------------------------------
# 3. Worktree SubAgent Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worktree_agent_list(git_repo: Path):
    agent = WorktreeAgent(workspace_dir=git_repo)
    task = SubAgentTask(
        agent_type=SubAgentType.WORKTREE,
        payload={"action": "list"},
    )
    result = await agent.run(task)

    assert result.success is True
    assert "worktrees" in result.output
    assert "total" in result.output


@pytest.mark.asyncio
async def test_worktree_agent_create_status_diff_discard(git_repo: Path):
    agent = WorktreeAgent(workspace_dir=git_repo)

    # 1. Create
    create_task = SubAgentTask(
        agent_type=SubAgentType.WORKTREE,
        payload={"action": "create", "branch": "feat/subagent-wt"},
    )
    create_res = await agent.run(create_task)
    assert create_res.success is True
    assert create_res.output["created"] is True
    wt_path = Path(create_res.output["worktree"]["path"])

    # 2. Status
    status_task = SubAgentTask(
        agent_type=SubAgentType.WORKTREE,
        payload={"action": "status", "branch": "feat/subagent-wt"},
    )
    status_res = await agent.run(status_task)
    assert status_res.success is True
    assert status_res.output["dirty"] is False

    # 3. Add file and Diff
    (wt_path / "test_sub.txt").write_text("subagent content", encoding="utf-8")
    subprocess.run(["git", "add", "test_sub.txt"], cwd=str(wt_path), check=True, capture_output=True)  # noqa: ASYNC221
    subprocess.run(["git", "commit", "-m", "subagent commit"], cwd=str(wt_path), check=True, capture_output=True)  # noqa: ASYNC221

    diff_task = SubAgentTask(
        agent_type=SubAgentType.WORKTREE,
        payload={"action": "diff", "branch": "feat/subagent-wt"},
    )
    diff_res = await agent.run(diff_task)
    assert diff_res.success is True
    assert "test_sub.txt" in diff_res.output["diff"]

    # 4. Discard
    discard_task = SubAgentTask(
        agent_type=SubAgentType.WORKTREE,
        payload={"action": "discard", "branch": "feat/subagent-wt", "delete_branch": True},
    )
    discard_res = await agent.run(discard_task)
    assert discard_res.success is True
    assert discard_res.output["removed"] is True


@pytest.mark.asyncio
async def test_worktree_agent_missing_params(git_repo: Path):
    agent = WorktreeAgent(workspace_dir=git_repo)

    task = SubAgentTask(
        agent_type=SubAgentType.WORKTREE,
        payload={"action": "create"},
    )
    res = await agent.run(task)
    assert res.success is False
    assert res.error is not None
    assert "Missing 'branch'" in res.error


@pytest.mark.asyncio
async def test_worktree_agent_unknown_action(git_repo: Path):
    agent = WorktreeAgent(workspace_dir=git_repo)

    task = SubAgentTask(
        agent_type=SubAgentType.WORKTREE,
        payload={"action": "unsupported_action"},
    )
    res = await agent.run(task)
    assert res.success is False
    assert res.error is not None
    assert "Unknown worktree action" in res.error


# ---------------------------------------------------------------------------
# 4. Tool Registry and Schema Tests
# ---------------------------------------------------------------------------


def test_tool_definitions_has_worktree():
    assert "worktree" in TOOL_DEFINITIONS
    wt_def = TOOL_DEFINITIONS["worktree"]
    assert "action" in wt_def["function"]["parameters"]["properties"]
    assert "branch" in wt_def["function"]["parameters"]["properties"]
    assert "strategy" in wt_def["function"]["parameters"]["properties"]

    tool_list = get_tool_definitions([SubAgentType.WORKTREE])
    assert len(tool_list) == 1
    assert tool_list[0]["function"]["name"] == "worktree"


def test_tool_registry_includes_worktree():
    registry = ToolRegistry()
    assert "worktree" in registry.registered_types()
    assert SubAgentType.WORKTREE.value == "worktree"


# ---------------------------------------------------------------------------
# 5. Slash Command Resolution Tests
# ---------------------------------------------------------------------------


def test_resolve_branch_slash_command():
    assert resolve_slash_command("/branch") == "/branch"
    assert resolve_slash_command("/branch list") == "/branch list"
    assert resolve_slash_command("\\branch create feat/test") == "/branch create feat/test"
    assert resolve_slash_command("/worktree diff") == "/branch diff"
    assert resolve_slash_command("/wt list") == "/branch list"
    assert resolve_slash_command("/branches") == "/branch"
