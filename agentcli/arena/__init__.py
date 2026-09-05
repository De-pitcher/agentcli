"""AgentCLI Arena and Benchmark Suite package."""

from agentcli.arena.evaluator import TaskEvaluator, TaskResult
from agentcli.arena.loader import TaskLoader, get_builtin_core_tasks
from agentcli.arena.runner import ArenaRunner, BenchmarkRunner
from agentcli.arena.scorecard import ScorecardFormatter
from agentcli.arena.task import BenchmarkTask, TaskCategory

__all__ = [
    "ArenaRunner",
    "BenchmarkRunner",
    "BenchmarkTask",
    "ScorecardFormatter",
    "TaskCategory",
    "TaskEvaluator",
    "TaskLoader",
    "TaskResult",
    "get_builtin_core_tasks",
]
