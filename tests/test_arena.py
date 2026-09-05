"""Unit and integration tests for AgentCLI Arena & Benchmark Suite (Phase 26)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentcli.arena.evaluator import TaskEvaluator, TaskResult
from agentcli.arena.loader import TaskLoader
from agentcli.arena.runner import ArenaRunner, BenchmarkRunner
from agentcli.arena.scorecard import ScorecardFormatter
from agentcli.arena.task import BenchmarkTask, TaskCategory
from agentcli.cli import main
from agentcli.config import Config
from agentcli.exit_codes import ExitCode


def test_benchmark_task_serialization() -> None:
    """Test BenchmarkTask dictionary conversion and deserialization."""
    task = BenchmarkTask(
        id="test_task_01",
        title="Test Task 01",
        category=TaskCategory.CODE_GEN,
        description="A sample test task",
        prompt="Write a file called test.py",
        workspace_setup={"file.txt": "hello world"},
        expected_files={"test.py": r"def main"},
        test_command="python -m unittest test.py",
        test_files={"test.py": "import unittest\n"},
        timeout_seconds=45,
        max_iterations=4,
        tags=["core", "quick"],
    )

    data = task.to_dict()
    assert data["id"] == "test_task_01"
    assert data["category"] == "code_gen"
    assert data["timeout_seconds"] == 45

    rebuilt = BenchmarkTask.from_dict(data)
    assert rebuilt.id == task.id
    assert rebuilt.category == TaskCategory.CODE_GEN
    assert rebuilt.workspace_setup == {"file.txt": "hello world"}
    assert rebuilt.tags == ["core", "quick"]


def test_task_loader_builtin_and_custom_json(tmp_path: Path) -> None:
    """Test TaskLoader loading built-ins and custom JSON suites."""
    loader = TaskLoader(custom_suites_dir=tmp_path)
    suites = loader.get_suites()
    assert "core" in suites
    core_tasks = suites["core"]
    assert len(core_tasks) >= 4

    # Create custom suite in JSON
    custom_tasks = [
        {
            "id": "custom_01",
            "title": "Custom Task 1",
            "category": "bug_fix",
            "description": "Fix bug",
            "prompt": "Fix it",
            "tags": ["custom", "test"],
        }
    ]
    custom_file = tmp_path / "custom_suite.json"
    custom_file.write_text(json.dumps(custom_tasks), encoding="utf-8")

    suites_updated = loader.get_suites()
    assert "custom_suite" in suites_updated
    assert len(suites_updated["custom_suite"]) == 1
    assert suites_updated["custom_suite"][0].id == "custom_01"

    # Filter tasks
    filtered = loader.filter_tasks(core_tasks, category=TaskCategory.BUG_FIX)
    assert all(t.category == TaskCategory.BUG_FIX for t in filtered)

    filtered_tag = loader.filter_tasks(core_tasks, tag="quick")
    assert len(filtered_tag) > 0


def test_task_evaluator_expected_files(tmp_path: Path) -> None:
    """Test TaskEvaluator file checking logic."""
    evaluator = TaskEvaluator()
    task = BenchmarkTask(
        id="eval_test",
        title="Eval Test",
        category=TaskCategory.CODE_GEN,
        description="test",
        prompt="prompt",
        expected_files={"src/output.txt": r"SUCCESS_\d+"},
    )

    # Missing file
    success, reason, _out = evaluator.evaluate(task, tmp_path)
    assert not success
    assert reason == "file_missing"

    # Pattern mismatch
    target_file = tmp_path / "src" / "output.txt"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("FAILURE", encoding="utf-8")

    success, reason, _out = evaluator.evaluate(task, tmp_path)
    assert not success
    assert reason == "file_pattern_mismatch"

    # Match
    target_file.write_text("SUCCESS_12345", encoding="utf-8")
    success, reason, _out = evaluator.evaluate(task, tmp_path)
    assert success
    assert reason == "success"


def test_task_evaluator_test_command(tmp_path: Path) -> None:
    """Test TaskEvaluator test command execution."""
    evaluator = TaskEvaluator()
    task = BenchmarkTask(
        id="cmd_test",
        title="Cmd Test",
        category=TaskCategory.CODE_GEN,
        description="test",
        prompt="prompt",
        test_command="python -c \"import sys; sys.exit(0)\"",
    )

    success, reason, _out = evaluator.evaluate(task, tmp_path)
    assert success
    assert reason == "success"

    # Failing test command
    task_fail = BenchmarkTask(
        id="cmd_fail",
        title="Cmd Fail",
        category=TaskCategory.CODE_GEN,
        description="test",
        prompt="prompt",
        test_command="python -c \"import sys; sys.exit(1)\"",
    )
    success, reason, _out = evaluator.evaluate(task_fail, tmp_path)
    assert not success
    assert reason == "test_failure"


def test_scorecard_formatter_output() -> None:
    """Test ScorecardFormatter ASCII, Markdown, and JSON rendering."""
    results = [
        TaskResult(
            task_id="task_1",
            task_title="Task 1",
            model="model-a",
            success=True,
            exit_reason="success",
            latency_seconds=1.25,
            turns_count=2,
            tool_calls_count=3,
            cost_usd=0.0012,
        ),
        TaskResult(
            task_id="task_2",
            task_title="Task 2",
            model="model-a",
            success=False,
            exit_reason="test_failure",
            latency_seconds=3.50,
            turns_count=5,
            tool_calls_count=4,
            cost_usd=0.0045,
        ),
    ]

    # Table format
    table = ScorecardFormatter.render_table(results, title="Unit Test Bench")
    assert "UNIT TEST BENCH" in table
    assert "task_1" in table
    assert "PASS" in table
    assert "FAIL" in table
    assert "50.0%" in table

    # Markdown format
    md = ScorecardFormatter.render_markdown_report("unit_suite", results)
    assert "# Benchmark Evaluation Report: `unit_suite`" in md
    assert "Passed Tasks**: 1 (50.0%)" in md
    assert "| `task_1` |" in md

    # Arena Leaderboard format
    arena_results = {
        "model-a": results,
        "model-b": [
            TaskResult(
                task_id="task_1",
                task_title="Task 1",
                model="model-b",
                success=True,
                exit_reason="success",
                latency_seconds=0.8,
                turns_count=1,
                tool_calls_count=1,
                cost_usd=0.0005,
            )
        ],
    }
    leaderboard = ScorecardFormatter.render_arena_leaderboard(arena_results)
    assert "# 🏆 AgentCLI Arena Leaderboard" in leaderboard
    assert "**model-b**" in leaderboard
    assert "**model-a**" in leaderboard

    # JSON export
    json_str = ScorecardFormatter.to_json(results)
    parsed = json.loads(json_str)
    assert len(parsed) == 2
    assert parsed[0]["task_id"] == "task_1"


def test_benchmark_runner_mock_execution() -> None:
    """Test BenchmarkRunner executing a task with mock planner/reflector."""
    config = Config()
    task = BenchmarkTask(
        id="runner_mock_task",
        title="Runner Mock",
        category=TaskCategory.CODE_GEN,
        description="Write a file",
        prompt="Write answer.txt containing 42",
        workspace_setup={},
        expected_files={"answer.txt": r"42"},
    )

    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.return_value = (True, "success", "Mock assertions passed")

    runner = BenchmarkRunner(config=config, model="test-model", evaluator=mock_evaluator)
    result = runner.run_task(task)

    assert result.task_id == "runner_mock_task"
    assert result.model == "test-model"
    assert result.success is True
    assert result.exit_reason == "success"
    assert result.latency_seconds >= 0.0


def test_arena_runner_comparison() -> None:
    """Test ArenaRunner running comparison across models."""
    config = Config()
    tasks = [
        BenchmarkTask(
            id="arena_t1",
            title="Arena T1",
            category=TaskCategory.CODE_GEN,
            description="T1",
            prompt="Prompt",
        )
    ]

    mock_eval = MagicMock()
    mock_eval.evaluate.return_value = (True, "success", "OK")

    arena_runner = ArenaRunner(config=config, evaluator=mock_eval)
    arena_results = arena_runner.run_comparison(tasks=tasks, models=["model_alpha", "model_beta"])

    assert "model_alpha" in arena_results
    assert "model_beta" in arena_results
    assert len(arena_results["model_alpha"]) == 1
    assert arena_results["model_alpha"][0].success is True


def test_cli_bench_list(capsys: pytest.CaptureFixture[str]) -> None:
    """Test `agentcli bench list` CLI command."""
    code = main(["bench", "list"])
    assert code == ExitCode.SUCCESS
    captured = capsys.readouterr().out
    assert "Available Benchmark Suites and Tasks:" in captured
    assert "Suite: core" in captured
    assert "humaneval_001_has_close_elements" in captured


def test_cli_bench_run_dry(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test `agentcli bench run` CLI command with custom file and output format."""
    custom_tasks = [
        {
            "id": "cli_task_01",
            "title": "CLI Task 01",
            "category": "code_gen",
            "description": "desc",
            "prompt": "prompt",
            "workspace_setup": {"result.txt": "MATCH_OK"},
            "expected_files": {"result.txt": r"MATCH_OK"},
        }
    ]
    task_file = tmp_path / "tasks.json"
    task_file.write_text(json.dumps(custom_tasks), encoding="utf-8")

    out_file = tmp_path / "report.md"
    code = main(["bench", "run", "--file", str(task_file), "--output", str(out_file), "--format", "markdown"])
    assert code == ExitCode.SUCCESS
    captured = capsys.readouterr().out
    assert "Starting benchmark run" in captured
    assert "cli_task_01" in captured
    assert out_file.exists()
    assert "# Benchmark Evaluation Report" in out_file.read_text(encoding="utf-8")


def test_cli_arena_compare(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test `agentcli arena compare` CLI command."""
    custom_tasks = [
        {
            "id": "arena_cli_01",
            "title": "Arena Task",
            "category": "code_gen",
            "description": "desc",
            "prompt": "prompt",
            "workspace_setup": {"test.txt": "VALID"},
            "expected_files": {"test.txt": r"VALID"},
        }
    ]
    task_file = tmp_path / "arena_tasks.json"
    task_file.write_text(json.dumps(custom_tasks), encoding="utf-8")

    out_leaderboard = tmp_path / "leaderboard.md"
    code = main([
        "arena",
        "compare",
        "--models",
        "mock-model-1,mock-model-2",
        "--file",
        str(task_file),
        "--output",
        str(out_leaderboard),
    ])
    assert code == ExitCode.SUCCESS
    captured = capsys.readouterr().out
    assert "Running Arena comparison across 2 model(s)" in captured
    assert "AgentCLI Arena Leaderboard" in captured
    assert out_leaderboard.exists()
    assert "mock-model-1" in out_leaderboard.read_text(encoding="utf-8")
