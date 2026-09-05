"""Tests for Phase 22: Autonomous Project Watcher & Continuous TDD Loop."""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcli.agent.events import FinishEvent, LoopErrorEvent
from agentcli.cli import build_parser
from agentcli.config import Config, load_config
from agentcli.exit_codes import ExitCode
from agentcli.watcher import (
    ContinuousTDDRunner,
    FileWatcher,
    TestExecutionResult,
    WorktreeManager,
    run_watch,
)


def test_watcher_config_defaults_and_parsing(tmp_path: Path):
    """Test WatcherConfig default values and configuration loading."""
    cfg = Config()
    assert cfg.watcher.enabled is True
    assert cfg.watcher.test_command == "python -m pytest"
    assert cfg.watcher.debounce_seconds == 1.5
    assert cfg.watcher.cooldown_seconds == 5.0
    assert cfg.watcher.auto_apply is False
    assert cfg.watcher.max_repair_iterations == 5
    assert cfg.watcher.budget_tier == "low"

    toml_content = """
[watcher]
enabled = true
test_command = "pytest -q"
paths = ["src", "tests"]
debounce_seconds = 2.0
cooldown_seconds = 3.5
auto_apply = true
max_cost_usd = 0.50
max_repair_iterations = 8
budget_tier = "medium"
model = "anthropic/claude-3.5-sonnet"
"""
    config_file = tmp_path / "agentcli.toml"
    config_file.write_text(toml_content, encoding="utf-8")

    loaded = load_config(path=config_file)
    assert loaded.watcher.test_command == "pytest -q"
    assert loaded.watcher.paths == ["src", "tests"]
    assert loaded.watcher.debounce_seconds == 2.0
    assert loaded.watcher.cooldown_seconds == 3.5
    assert loaded.watcher.auto_apply is True
    assert loaded.watcher.max_cost_usd == 0.50
    assert loaded.watcher.max_repair_iterations == 8
    assert loaded.watcher.budget_tier == "medium"
    assert loaded.watcher.model == "anthropic/claude-3.5-sonnet"


def test_file_watcher_scan_and_change_detection(tmp_path: Path):
    """Test FileWatcher scanning, change detection, and directory ignore filtering."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f1 = src_dir / "app.py"
    f1.write_text("print('hello')", encoding="utf-8")

    # Ignored directory
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "index.py").write_text("git internal", encoding="utf-8")

    # Worktree directory
    wt_dir = tmp_path / ".agentcli_worktrees"
    wt_dir.mkdir()
    (wt_dir / "wt.py").write_text("wt file", encoding="utf-8")

    watcher = FileWatcher(paths=[tmp_path], debounce_seconds=0.1)
    initial_scan = watcher.scan()

    assert f1.resolve() in initial_scan
    # Ignored paths should not be present
    assert (git_dir / "index.py").resolve() not in initial_scan
    assert (wt_dir / "wt.py").resolve() not in initial_scan

    # Detect no changes initially
    assert len(watcher.detect_changes()) == 0

    # Modify existing file
    time.sleep(0.05)
    f1.write_text("print('hello world')", encoding="utf-8")
    os.utime(f1, (time.time() + 1.0, time.time() + 1.0))
    changes = watcher.detect_changes()
    assert f1.resolve() in changes

    # Add new file
    f2 = src_dir / "utils.py"
    f2.write_text("def add(a, b): return a + b", encoding="utf-8")
    changes = watcher.detect_changes()
    assert f2.resolve() in changes

    # Delete a file
    f2.unlink()
    changes = watcher.detect_changes()
    assert f2.resolve() in changes


@pytest.mark.asyncio
async def test_file_watcher_async_watch_debouncing(tmp_path: Path):
    """Test FileWatcher async generator yields debounced batches."""
    f1 = tmp_path / "main.py"
    f1.write_text("x = 1", encoding="utf-8")

    watcher = FileWatcher(paths=[tmp_path], debounce_seconds=0.1)

    async def trigger_changes():
        await asyncio.sleep(0.05)
        f1.write_text("x = 2", encoding="utf-8")
        os.utime(f1, (time.time() + 2.0, time.time() + 2.0))
        await asyncio.sleep(0.02)
        f1.write_text("x = 3", encoding="utf-8")
        os.utime(f1, (time.time() + 3.0, time.time() + 3.0))

    task = asyncio.create_task(trigger_changes())

    batches = []
    async for change_batch in watcher.watch(poll_interval=0.02):
        batches.append(change_batch)
        if len(batches) >= 1:
            watcher.stop()

    await task
    assert len(batches) >= 1
    assert f1.resolve() in batches[0]


@pytest.mark.asyncio
async def test_worktree_manager_git_lifecycle(tmp_path: Path):
    """Test WorktreeManager methods with mocked subprocess calls."""
    mgr = WorktreeManager(root_dir=tmp_path)

    # Mock is_git_repo
    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0, stdout="true\n")
        assert mgr.is_git_repo() is True

        mock_sub.return_value = MagicMock(returncode=1, stdout="")
        assert mgr.is_git_repo() is False

    # Mock create_worktree and remove_worktree
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        wt_dir, branch = await mgr.create_worktree(branch_prefix="test-repair")
        assert "test-repair" in branch
        assert wt_dir.parent == (tmp_path / ".agentcli_worktrees")

        patch_text = await mgr.get_patch(wt_dir)
        assert patch_text == ""

        apply_res = await mgr.apply_patch("diff --git a/f b/f\n")
        assert apply_res is True

        removed = await mgr.remove_worktree(wt_dir, branch_name=branch)
        assert removed is True


@pytest.mark.asyncio
async def test_continuous_tdd_runner_test_execution(tmp_path: Path):
    """Test ContinuousTDDRunner test command execution and failure summaries."""
    config = Config()
    config.watcher.test_command = "echo passed"

    runner = ContinuousTDDRunner(config=config, root_dir=tmp_path)

    # Mock passing test
    res = await runner.run_tests()
    assert res.passed is True
    assert res.return_code == 0

    # Mock failing test
    runner.watcher_config.test_command = "python -c \"import sys; sys.stderr.write('FAILED tests/test_app.py::test_fail - AssertionError'); sys.exit(1)\""
    fail_res = await runner.run_tests()
    assert fail_res.passed is False
    assert fail_res.return_code == 1
    assert "FAILED" in fail_res.failure_summary or "AssertionError" in fail_res.failure_summary


@pytest.mark.asyncio
async def test_continuous_tdd_runner_attempt_repair_success(tmp_path: Path):
    """Test attempt_repair creates worktree, runs AgentLoop, verifies fix, and applies patch."""
    config = Config()
    config.watcher.auto_apply = True
    config.watcher.max_repair_iterations = 3
    config.watcher.max_cost_usd = 1.0

    runner = ContinuousTDDRunner(config=config, root_dir=tmp_path)

    fake_wt = tmp_path / ".agentcli_worktrees" / "test-repair-1"

    # Mock AgentLoop
    async def mock_loop_run(self):
        self.cumulative_cost_usd = 0.05
        yield FinishEvent(summary="Fixed issue in file.py")

    failure = TestExecutionResult(
        passed=False,
        return_code=1,
        stdout="FAILED tests/test_app.py",
        stderr="AssertionError",
        duration_seconds=0.5,
        failure_summary="FAILED tests/test_app.py",
    )

    async def fake_run_tests(cwd=None, timeout=120.0):
        if cwd == fake_wt:
            return TestExecutionResult(
                passed=True,
                return_code=0,
                stdout="1 passed",
                stderr="",
                duration_seconds=0.2,
            )
        return failure

    with (
        patch.object(runner.worktree_manager, "is_git_repo", return_value=True),
        patch.object(runner.worktree_manager, "create_worktree", new_callable=AsyncMock) as mock_create_wt,
        patch.object(runner.worktree_manager, "remove_worktree", new_callable=AsyncMock) as mock_remove_wt,
        patch.object(runner.worktree_manager, "get_patch", new_callable=AsyncMock) as mock_get_patch,
        patch.object(runner.worktree_manager, "apply_patch", new_callable=AsyncMock) as mock_apply_patch,
        patch.object(runner, "run_tests", side_effect=fake_run_tests),
        patch("agentcli.watcher.AgentLoop.run", mock_loop_run),
    ):
        mock_create_wt.return_value = (fake_wt, "test-repair-1")
        mock_remove_wt.return_value = True
        mock_get_patch.return_value = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-broken\n+fixed\n"
        mock_apply_patch.return_value = True

        success = await runner.attempt_repair(failure, changed_files={tmp_path / "file.py"})
        assert success is True
        assert runner.cumulative_cost_usd == 0.05
        mock_apply_patch.assert_awaited_once()
        mock_remove_wt.assert_awaited_once()


@pytest.mark.asyncio
async def test_continuous_tdd_runner_budget_ceiling(tmp_path: Path):
    """Test attempt_repair enforces max_cost_usd ceiling."""
    config = Config()
    config.watcher.max_cost_usd = 0.10

    runner = ContinuousTDDRunner(config=config, root_dir=tmp_path)
    runner.cumulative_cost_usd = 0.15  # Already over budget

    failure = TestExecutionResult(
        passed=False,
        return_code=1,
        stdout="",
        stderr="Failed",
        duration_seconds=0.1,
    )

    success = await runner.attempt_repair(failure)
    assert success is False


@pytest.mark.asyncio
async def test_continuous_tdd_runner_run_loop(tmp_path: Path):
    """Test ContinuousTDDRunner run loop responds to file events."""
    config = Config()
    config.watcher.cooldown_seconds = 0.01
    config.watcher.debounce_seconds = 0.01

    runner = ContinuousTDDRunner(config=config, root_dir=tmp_path)

    test_res = TestExecutionResult(
        passed=True,
        return_code=0,
        stdout="OK",
        stderr="",
        duration_seconds=0.1,
    )

    async def mock_watch():
        yield {tmp_path / "app.py"}
        runner.stop()

    with (
        patch.object(runner, "run_tests", new_callable=AsyncMock) as mock_run_tests,
        patch.object(runner.watcher, "watch", side_effect=mock_watch),
    ):
        mock_run_tests.return_value = test_res
        exit_code = await runner.run(run_initial=True)
        assert exit_code == ExitCode.SUCCESS
        assert mock_run_tests.await_count >= 2


def test_cli_build_parser_watch_subcommand():
    """Test CLI parser recognizes 'watch' subcommand and arguments."""
    parser = build_parser()
    args = parser.parse_args([
        "watch",
        "--test-cmd", "pytest tests/fast",
        "--debounce", "2.5",
        "--cooldown", "6.0",
        "--auto-apply",
        "--max-cost", "1.25",
        "--budget", "high",
        "--model", "anthropic/claude-3-opus",
        "--max-iterations", "7",
        "--paths", "src",
        "--paths", "tests",
        "--no-initial",
    ])

    assert args.command == "watch"
    assert args.test_cmd == "pytest tests/fast"
    assert args.debounce == 2.5
    assert args.cooldown == 6.0
    assert args.auto_apply is True
    assert args.max_cost == 1.25
    assert args.budget == "high"
    assert args.model == "anthropic/claude-3-opus"
    assert args.max_iterations == 7
    assert args.paths == ["src", "tests"]
    assert args.no_initial is True


@pytest.mark.asyncio
async def test_run_watch_entrypoint(monkeypatch):
    """Test run_watch entrypoint sets up runner and executes."""
    args = argparse.Namespace(
        command="watch",
        test_cmd="pytest",
        debounce=1.0,
        cooldown=2.0,
        auto_apply=True,
        max_cost=0.5,
        budget="medium",
        model=None,
        max_iterations=4,
        paths=["."],
        no_initial=True,
        plain=True,
        no_color=True,
    )
    config = Config()

    with patch.object(ContinuousTDDRunner, "run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = ExitCode.SUCCESS
        res = await run_watch(args, config)
        assert res == ExitCode.SUCCESS
        mock_run.assert_awaited_once_with(run_initial=False)


@pytest.mark.asyncio
async def test_continuous_tdd_runner_attempt_repair_failure_flow(tmp_path: Path):
    """Test attempt_repair handles verification failure and non-git repository gracefully."""
    config = Config()
    config.watcher.auto_apply = False
    runner = ContinuousTDDRunner(config=config, root_dir=tmp_path)

    fake_wt = tmp_path / ".agentcli_worktrees" / "test-repair-2"

    failure = TestExecutionResult(
        passed=False,
        return_code=1,
        stdout="FAIL",
        stderr="Error",
        duration_seconds=0.1,
    )

    # 1. Non-git repo branch
    with patch.object(runner.worktree_manager, "is_git_repo", return_value=False):
        assert await runner.attempt_repair(failure) is False

    # 2. Repair loop error event & repair verification still failing
    async def mock_loop_error(self):
        yield LoopErrorEvent(error="Model failed to produce plan")

    with (
        patch.object(runner.worktree_manager, "is_git_repo", return_value=True),
        patch.object(runner.worktree_manager, "create_worktree", new_callable=AsyncMock) as mock_create_wt,
        patch.object(runner.worktree_manager, "remove_worktree", new_callable=AsyncMock) as mock_remove_wt,
        patch.object(runner, "run_tests", new_callable=AsyncMock) as mock_run_tests,
        patch("agentcli.watcher.AgentLoop.run", mock_loop_error),
    ):
        mock_create_wt.return_value = (fake_wt, "test-repair-2")
        mock_remove_wt.return_value = True
        mock_run_tests.return_value = failure  # Still failing

        success = await runner.attempt_repair(failure)
        assert success is False
        mock_remove_wt.assert_awaited_once()


@pytest.mark.asyncio
async def test_continuous_tdd_runner_manual_patch_preview(tmp_path: Path):
    """Test attempt_repair previews patch when auto_apply is False."""
    config = Config()
    config.watcher.auto_apply = False
    runner = ContinuousTDDRunner(config=config, root_dir=tmp_path)

    fake_wt = tmp_path / ".agentcli_worktrees" / "test-repair-3"

    async def mock_loop_success(self):
        yield FinishEvent(summary="Fixed issue")

    failure = TestExecutionResult(
        passed=False,
        return_code=1,
        stdout="FAIL",
        stderr="Error",
        duration_seconds=0.1,
    )

    with (
        patch.object(runner.worktree_manager, "is_git_repo", return_value=True),
        patch.object(runner.worktree_manager, "create_worktree", new_callable=AsyncMock) as mock_create_wt,
        patch.object(runner.worktree_manager, "remove_worktree", new_callable=AsyncMock) as mock_remove_wt,
        patch.object(runner.worktree_manager, "get_patch", new_callable=AsyncMock) as mock_get_patch,
        patch.object(runner, "run_tests", new_callable=AsyncMock) as mock_run_tests,
        patch("agentcli.watcher.AgentLoop.run", mock_loop_success),
    ):
        mock_create_wt.return_value = (fake_wt, "test-repair-3")
        mock_remove_wt.return_value = True
        mock_get_patch.return_value = "diff --git a/app.py b/app.py\n+fixed\n"
        mock_run_tests.return_value = TestExecutionResult(
            passed=True,
            return_code=0,
            stdout="PASS",
            stderr="",
            duration_seconds=0.1,
        )

        success = await runner.attempt_repair(failure)
        assert success is True
        mock_remove_wt.assert_awaited_once()


@pytest.mark.asyncio
async def test_worktree_manager_create_error(tmp_path: Path):
    """Test WorktreeManager raises RuntimeError when git worktree creation fails."""
    mgr = WorktreeManager(root_dir=tmp_path)

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"fatal: git worktree add failed")
    mock_proc.returncode = 1

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(RuntimeError, match="Failed to create git worktree"),
    ):
        await mgr.create_worktree()


def test_file_watcher_scan_single_file(tmp_path: Path):
    """Test FileWatcher directly watching a single file path."""
    f = tmp_path / "single.py"
    f.write_text("x = 10", encoding="utf-8")

    watcher = FileWatcher(paths=[f])
    scan = watcher.scan()
    assert f.resolve() in scan


def test_continuous_tdd_runner_logging(tmp_path: Path):
    """Test ContinuousTDDRunner logging helpers."""
    runner = ContinuousTDDRunner(config=Config(), root_dir=tmp_path)
    runner._log_info("Info message")
    runner._log_success("Success message")
    runner._log_warning("Warning message")
    runner._log_error("Error message")

