"""Test suite for production field trials and usability scorecards (Phase 30)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcli.arena.evaluator import TaskEvaluator, TaskResult
from agentcli.arena.loader import TaskLoader, get_builtin_field_trials_tasks
from agentcli.arena.scorecard import ScorecardFormatter


@pytest.fixture(autouse=True)
def set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-api-key")


def test_field_trials_suite_loading() -> None:
    """Test get_builtin_field_trials_tasks returns valid benchmark tasks."""
    tasks = get_builtin_field_trials_tasks()
    assert len(tasks) == 4

    task_ids = [t.id for t in tasks]
    assert "field_bugfix_auth_jwt" in task_ids
    assert "field_refactor_lru_cache" in task_ids
    assert "field_mesh_cross_dependency" in task_ids
    assert "field_tool_log_metrics" in task_ids

    loader = TaskLoader()
    suites = loader.get_suites()
    assert "field_trials" in suites
    assert len(suites["field_trials"]) == 4


def test_scorecard_percentile_calculations() -> None:
    """Test calculation of p50 and p95 percentiles."""
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    p50, p95 = ScorecardFormatter.calculate_percentiles(values)
    assert p50 == 6.0
    assert p95 == 10.0

    empty_p50, empty_p95 = ScorecardFormatter.calculate_percentiles([])
    assert empty_p50 == 0.0
    assert empty_p95 == 0.0


def test_field_trial_scorecard_rendering() -> None:
    """Test render_field_trial_summary output formatting."""
    results = [
        TaskResult(
            task_id="field_bugfix_auth_jwt",
            task_title="Multi-File Auth Repair",
            model="google/gemma-4-31b-it:free",
            success=True,
            exit_reason="GOAL_COMPLETE",
            latency_seconds=1.45,
            turns_count=2,
            tool_calls_count=3,
            cost_usd=0.0025,
        ),
        TaskResult(
            task_id="field_refactor_lru_cache",
            task_title="LRU Cache Eviction",
            model="google/gemma-4-31b-it:free",
            success=True,
            exit_reason="GOAL_COMPLETE",
            latency_seconds=2.10,
            turns_count=3,
            tool_calls_count=4,
            cost_usd=0.0035,
        ),
    ]

    summary = ScorecardFormatter.render_field_trial_summary(results)
    assert "AGENTCLI PRODUCTION FIELD TRIAL SCORECARD" in summary
    assert "Pass@1 Success Rate : 100.0% (2/2 tasks passed)" in summary
    assert "Turn Latency (p50)" in summary
    assert "field_bugfix_auth_jwt" in summary
    assert "field_refactor_lru_cache" in summary


def test_field_trial_sandbox_evaluation(tmp_path: Path) -> None:
    """Test TaskEvaluator on a field trial task setup."""
    tasks = get_builtin_field_trials_tasks()
    task = tasks[0]  # field_bugfix_auth_jwt

    # Create workspace and write solution directly
    ws = tmp_path / "sandbox"
    ws.mkdir()
    for rel_path, content in task.workspace_setup.items():
        p = ws / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    # Fix the bug in src/auth/jwt.py
    jwt_file = ws / "src" / "auth" / "jwt.py"
    jwt_file.write_text(
        "def extract_bearer_token(header: str | None) -> str | None:\n"
        "    if not header:\n"
        "        return None\n"
        "    cleaned = header.strip()\n"
        "    if cleaned.lower().startswith('bearer '):\n"
        "        return cleaned[7:].strip()\n"
        "    return None\n",
        encoding="utf-8",
    )

    evaluator = TaskEvaluator()
    success, reason, stdout = evaluator.evaluate(task, ws)
    assert success is True
    assert reason == "success"
    assert "OK" in stdout or "Ran 2 tests" in stdout or "passed" in stdout.lower() or not stdout
