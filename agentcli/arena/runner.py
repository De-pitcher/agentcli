"""Benchmark and Arena execution runners for AgentCLI."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from agentcli.agent.events import LoopErrorEvent, PlanEvent, StepStartEvent
from agentcli.agent.loop import AgentLoop
from agentcli.agent.protocols import PlannerProtocol
from agentcli.agent.registry import ToolRegistry
from agentcli.arena.evaluator import TaskEvaluator, TaskResult
from agentcli.arena.task import BenchmarkTask
from agentcli.config import Config


class BenchmarkRunner:
    """Runs individual benchmark tasks in isolated temporary workspaces."""

    def __init__(
        self,
        config: Config | None = None,
        model: str | None = None,
        evaluator: TaskEvaluator | None = None,
        planner: PlannerProtocol | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.config = config or Config()
        self.model = model or self.config.openrouter.default_model
        self.evaluator = evaluator or TaskEvaluator()
        self.planner = planner
        self.registry = registry

    def _drive_agent_in_workspace(self, task: BenchmarkTask, temp_dir: Path) -> tuple[int, int, float, str | None]:
        """Drive the agent loop inside the isolated workspace."""
        old_cwd = os.getcwd()
        turns = 0
        tools = 0
        cost = 0.0
        err: str | None = None

        try:
            os.chdir(str(temp_dir))
            loop = AgentLoop(
                goal=task.prompt,
                registry=self.registry or ToolRegistry(),
                planner=self.planner,
                max_iterations=task.max_iterations,
                config=self.config,
                plan_model=self.model,
            )

            async def _drive() -> None:
                nonlocal turns, tools, cost, err
                try:
                    async for ev in loop.run():
                        if isinstance(ev, StepStartEvent):
                            tools += 1
                        elif isinstance(ev, PlanEvent):
                            turns += 1
                        elif isinstance(ev, LoopErrorEvent):
                            err = ev.error
                    cost = loop.cumulative_cost_usd
                except Exception as loop_e:  # noqa: BLE001
                    err = str(loop_e)

            # Check if there is already a running event loop (e.g. in async test)
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            if running_loop and running_loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    fut = executor.submit(lambda: asyncio.run(_drive()))
                    fut.result(timeout=task.timeout_seconds + 5)
            else:
                asyncio.run(_drive())

        except Exception as exc:  # noqa: BLE001
            err = str(exc)
        finally:
            os.chdir(old_cwd)

        return turns, tools, cost, err

    def run_task(self, task: BenchmarkTask) -> TaskResult:
        """Execute a single benchmark task in an isolated sandbox and evaluate the outcome."""
        temp_dir = Path(tempfile.mkdtemp(prefix=f"bench_{task.id}_"))
        start_time = time.time()
        turns_count = 0
        tool_calls_count = 0
        cost_usd = 0.0
        exit_reason = "unknown"
        error_msg: str | None = None
        verification_stdout = ""

        try:
            # 1. Setup initial workspace files
            for rel_path, content in task.workspace_setup.items():
                dest_file = temp_dir / rel_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                dest_file.write_text(content, encoding="utf-8")

            # 2. Drive agent loop in workspace if planner or mock available
            turns_count, tool_calls_count, cost_usd, error_msg = self._drive_agent_in_workspace(task, temp_dir)

            # 3. Evaluate task assertions and tests
            success, exit_reason, verification_stdout = self.evaluator.evaluate(
                task=task,
                workspace_dir=temp_dir,
                verification_timeout=task.timeout_seconds,
            )

        except Exception as e:  # noqa: BLE001
            success = False
            exit_reason = "runner_exception"
            error_msg = str(e)
        finally:
            latency = time.time() - start_time
            # Cleanup sandbox
            shutil.rmtree(temp_dir, ignore_errors=True)

        return TaskResult(
            task_id=task.id,
            task_title=task.title,
            model=self.model,
            success=success,
            exit_reason=exit_reason,
            latency_seconds=latency,
            turns_count=turns_count,
            tool_calls_count=tool_calls_count,
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=cost_usd,
            error_message=error_msg,
            verification_stdout=verification_stdout,
        )

    def run_suite(
        self,
        tasks: list[BenchmarkTask],
        progress_callback: Callable[[int, int, BenchmarkTask, TaskResult], None] | None = None,
    ) -> list[TaskResult]:
        """Run a collection of benchmark tasks sequentially."""
        results: list[TaskResult] = []
        total = len(tasks)
        for idx, task in enumerate(tasks, start=1):
            res = self.run_task(task)
            results.append(res)
            if progress_callback:
                progress_callback(idx, total, task, res)
        return results


class ArenaRunner:
    """Coordinates multi-model evaluation and head-to-head benchmarking."""

    def __init__(
        self,
        config: Config | None = None,
        evaluator: TaskEvaluator | None = None,
        planner: PlannerProtocol | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.config = config or Config()
        self.evaluator = evaluator or TaskEvaluator()
        self.planner = planner
        self.registry = registry

    def run_comparison(
        self,
        tasks: list[BenchmarkTask],
        models: list[str],
        progress_callback: Callable[[str, int, int, BenchmarkTask, TaskResult], None] | None = None,
    ) -> dict[str, list[TaskResult]]:
        """Run benchmark suite across multiple candidate models."""
        arena_results: dict[str, list[TaskResult]] = {}
        for model in models:
            runner = BenchmarkRunner(
                config=self.config,
                model=model,
                evaluator=self.evaluator,
                planner=self.planner,
                registry=self.registry,
            )
            total = len(tasks)
            model_results: list[TaskResult] = []
            for idx, task in enumerate(tasks, start=1):
                res = runner.run_task(task)
                model_results.append(res)
                if progress_callback:
                    progress_callback(model, idx, total, task, res)
            arena_results[model] = model_results
        return arena_results
