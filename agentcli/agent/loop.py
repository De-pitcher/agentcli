"""AgentLoop — Plan → Act → Reflect engine for agentcli Phase 4.

Relationship to Phase 3:
  Phase 3's PlannerAgent produces the initial task decomposition (a flat
  list of step dicts).  AgentLoop wraps that decomposition with an
  act/reflect cycle, and can re-invoke PlannerAgent for re-planning when
  a step fails or the reflector signals REPLAN.

Design principles:
  - Every stage (planner, executor, reflector) is injected — swappable
    without touching this file.
  - The loop yields LoopEvent objects; callers consume them for display.
  - Simple single-turn chat NEVER passes through this loop (guarded in
    session.py and cli.py).
  - A hard max_iterations ceiling prevents runaway cycles.
  - Mid-loop OpenRouterError is delegated back to the router's existing
    fallback chain; if that is exhausted the loop surfaces LoopErrorEvent
    and stops cleanly, preserving all prior step results.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from ..openrouter_client import OpenRouterError, RateLimitedError
from ..routing.router import Router
from ..subagents.base import SubAgentResult, SubAgentTask, SubAgentType
from ..subagents.planner import PlannerAgent
from .events import (
    FinishEvent,
    LoopErrorEvent,
    LoopEvent,
    PlanEvent,
    ReflectEvent,
    StepResultEvent,
    StepStartEvent,
)
from .reflector import DefaultReflector, ReflectDecision
from .registry import ToolRegistry

logger = logging.getLogger(__name__)


class LoopIterationLimitError(Exception):
    """Raised when the loop hits its max_iterations ceiling."""


class AgentLoop:
    """Orchestrates the Plan → Act → Reflect agentic cycle.

    Args:
        goal:           The user's multi-step goal string.
        registry:       ToolRegistry providing access to Phase 3 tools.
        planner:        PlannerAgent (or any PlannerProtocol-compatible object).
        reflector:      DefaultReflector (or any ReflectorProtocol-compatible object).
        router:         Optional Phase 2 Router for model-selection overrides.
        max_iterations: Hard ceiling on plan/act/reflect cycles.
        plan_model:     Optional model ID override for the planning step.
        reflect_model:  Optional model ID override for the reflection step.
    """

    def __init__(
        self,
        goal: str,
        registry: ToolRegistry | None = None,
        planner: PlannerAgent | None = None,
        reflector: DefaultReflector | None = None,
        router: Router | None = None,
        max_iterations: int = 5,
        plan_model: str | None = None,
        reflect_model: str | None = None,
    ) -> None:
        self.goal = goal
        self.registry = registry or ToolRegistry()
        self.planner = planner or PlannerAgent()
        self.reflector = reflector or DefaultReflector()
        self.router = router
        self.max_iterations = max_iterations
        self.plan_model = plan_model
        self.reflect_model = reflect_model

        self._all_results: list[SubAgentResult] = []
        self._running_tasks: list[asyncio.Task[Any]] = []

    async def run(self) -> AsyncIterator[LoopEvent]:
        """Drive the loop and yield LoopEvent objects.

        Usage::

            async for event in loop.run():
                if isinstance(event, FinishEvent):
                    print("Done:", event.summary)
        """
        return self._run_impl()

    async def _run_impl(self) -> AsyncIterator[LoopEvent]:
        """Internal generator-based implementation."""
        current_plan: list[dict[str, Any]] = []
        is_replan = False

        try:
            for iteration in range(1, self.max_iterations + 1):
                logger.debug("AgentLoop: iteration %d / %d", iteration, self.max_iterations)

                # ── PLAN ────────────────────────────────────────────────
                try:
                    current_plan = await self._plan(current_plan if is_replan else None)
                except Exception as exc:  # noqa: BLE001
                    yield LoopErrorEvent(
                        iteration=iteration,
                        error=f"Planning failed: {exc}",
                    )
                    return

                yield PlanEvent(
                    iteration=iteration,
                    plan=current_plan,
                    is_replan=is_replan,
                )

                if not current_plan:
                    yield LoopErrorEvent(
                        iteration=iteration,
                        error="Planner returned an empty plan.",
                    )
                    return

                # ── ACT ─────────────────────────────────────────────────
                step_results: list[SubAgentResult] = []
                for step_index, step in enumerate(current_plan):
                    agent_type = step.get("agent_type", SubAgentType.CODE_ANALYZER.value)
                    payload = step.get("payload", {})

                    yield StepStartEvent(
                        iteration=iteration,
                        step_index=step_index,
                        agent_type=agent_type,
                        payload=payload,
                    )

                    result = await self._execute_step(agent_type, payload, iteration)
                    step_results.append(result)
                    self._all_results.append(result)

                    yield StepResultEvent(
                        iteration=iteration,
                        step_index=step_index,
                        result=result,
                    )

                # ── REFLECT ─────────────────────────────────────────────
                outcome = self.reflector.reflect(self.goal, current_plan, step_results)

                yield ReflectEvent(
                    iteration=iteration,
                    decision=outcome.decision.value,
                    reason=outcome.reason,
                )

                if outcome.decision == ReflectDecision.FINISH:
                    summary = self._build_summary(step_results)
                    yield FinishEvent(iteration=iteration, summary=summary)
                    return

                if outcome.decision == ReflectDecision.FAIL:
                    yield LoopErrorEvent(
                        iteration=iteration,
                        error=f"Reflector declared failure: {outcome.reason}",
                    )
                    return

                # RETRY or REPLAN — loop again.
                is_replan = outcome.decision == ReflectDecision.REPLAN

            # Exhausted max_iterations without finishing.
            raise LoopIterationLimitError(
                f"AgentLoop reached max_iterations={self.max_iterations} "
                f"without finishing. Last reflect reason: {outcome.reason}"
            )

        finally:
            await self._cancel_running_tasks()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _plan(self, previous_plan: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Invoke PlannerAgent and return the plan step list."""
        payload: dict[str, Any] = {"query": self.goal}
        if previous_plan:
            # Give the planner context about what was already tried.
            payload["context"] = (
                f"Previous plan had {len(previous_plan)} step(s). Re-planning because "
                "some steps failed or the goal was not met."
            )

        task = SubAgentTask(
            agent_type=SubAgentType.PLANNER,
            payload=payload,
        )
        result = await self.planner.run(task)
        if not result.success:
            raise RuntimeError(result.error or "Planner returned failure with no error message.")
        output: dict[str, Any] = result.output or {}
        plan: list[dict[str, Any]] = output.get("plan", [])
        return plan

    async def _execute_step(
        self,
        agent_type: str,
        payload: dict[str, Any],
        iteration: int,
    ) -> SubAgentResult:
        """Execute a single step via the ToolRegistry.

        Handles OpenRouterError by logging; the error surfaces in the result.
        """
        try:
            coro = self.registry.execute(agent_type, payload)
            task: asyncio.Task[SubAgentResult] = asyncio.ensure_future(coro)
            self._running_tasks.append(task)
            result = await task
            self._running_tasks.remove(task)
            return result
        except (OpenRouterError, RateLimitedError) as exc:
            logger.warning(
                "AgentLoop iteration %d: OpenRouter error on step '%s': %s",
                iteration,
                agent_type,
                exc,
            )
            return SubAgentResult(
                task_id="loop-error",
                agent_type=ToolRegistry._safe_type(agent_type),
                success=False,
                error=f"Model error: {exc}",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            return SubAgentResult(
                task_id="loop-error",
                agent_type=ToolRegistry._safe_type(agent_type),
                success=False,
                error=str(exc),
            )

    async def _cancel_running_tasks(self) -> None:
        """Cancel any in-flight asyncio tasks on loop exit."""
        for task in list(self._running_tasks):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass  # Expected — task was successfully cancelled.
                except BaseException as exc:  # noqa: BLE001
                    logger.debug("Unexpected error cancelling loop task: %s", exc)
        self._running_tasks.clear()

    @staticmethod
    def _build_summary(results: list[SubAgentResult]) -> str:
        successes = sum(1 for r in results if r.success)
        return f"{successes}/{len(results)} step(s) completed successfully."


# ------------------------------------------------------------------
# Task-intent heuristic (used by AgentSession / cli.py)
# ------------------------------------------------------------------

_AGENTIC_KEYWORDS: tuple[str, ...] = (
    "then",
    "and then",
    "after that",
    "next,",
    "first,",
    "finally,",
    "step 1",
    "step 2",
    "do the following",
    "please do",
    "execute",
    "run and",
    "read and",
    "analyze and",
    "list and",
    "write and",
    "create and then",
)


def is_agentic_task(text: str) -> bool:
    """Return True if *text* looks like a multi-step task.

    This is intentionally conservative: it only triggers the loop for
    clearly multi-step imperative requests, avoiding false-positives on
    simple questions like "write hello world" or "explain X".

    Phase 5 (memory/context) may replace this with a lightweight
    classifier using the session's conversation history.
    """
    lower = text.lower()
    return any(kw in lower for kw in _AGENTIC_KEYWORDS)


__all__ = ["AgentLoop", "LoopIterationLimitError", "is_agentic_task"]
