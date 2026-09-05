"""AgentLoop — Plan → Act → Reflect engine for agentcli Phase 4.

Relationship to Phase 3:
  Phase 3's PlannerAgent produces the initial task decomposition (a flat
  list of step dicts).  AgentLoop wraps that decomposition with an
  act/reflect cycle, and can re-invoke PlannerAgent for re-planning when
  a step fails or the reflector signals REPLAN.

Design principles:
  - Every stage (planner, executor, reflector) is injected via Protocols —
    swappable without touching this file.
  - The loop yields LoopEvent objects; callers consume them for display.
  - Simple single-turn chat NEVER passes through this loop (guarded in
    session.py and cli.py).
  - A hard max_iterations ceiling prevents runaway cycles.
  - Model selection and overrides integrate with the Phase 2 router for
    fallback candidate chains.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
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
from .protocols import ExecutorProtocol, PlannerProtocol, ReflectorProtocol
from .reflector import DefaultReflector, ReflectDecision
from .registry import ToolRegistry

logger = logging.getLogger(__name__)


class LoopIterationLimitError(Exception):
    """Raised when the loop hits its max_iterations ceiling."""


class AgentLoop:
    """Orchestrates the Plan → Act → Reflect agentic cycle.

    Args:
        goal:           The user's multi-step goal string.
        registry:       ExecutorProtocol providing access to tools (default: ToolRegistry).
        planner:        PlannerProtocol decomposing tasks (default: PlannerAgent).
        reflector:      ReflectorProtocol evaluating outcomes (default: DefaultReflector).
        router:         Optional Phase 2 Router for model-selection overrides.
        max_iterations: Hard ceiling on plan/act/reflect cycles.
        plan_model:     Optional model ID override for the planning step.
        reflect_model:  Optional model ID override for the reflection step.
        config:         Optional resolved application configuration.
        run_id:         Optional correlation run identifier for observability.
    """

    def __init__(
        self,
        goal: str,
        registry: ExecutorProtocol | None = None,
        planner: PlannerProtocol | None = None,
        reflector: ReflectorProtocol | None = None,
        router: Router | None = None,
        max_iterations: int = 5,
        plan_model: str | None = None,
        reflect_model: str | None = None,
        config: Any | None = None,
        run_id: str | None = None,
        initial_context: str | None = None,
        max_cost_usd: float | None = None,
    ) -> None:
        self.goal = goal
        self.registry: ExecutorProtocol = registry if registry is not None else ToolRegistry()
        self.planner: PlannerProtocol = planner if planner is not None else PlannerAgent()
        self.reflector: ReflectorProtocol = (
            reflector if reflector is not None else DefaultReflector()
        )
        self.router = router
        self.max_iterations = max_iterations
        self.plan_model = plan_model
        self.reflect_model = reflect_model
        self._config = config
        self.run_id: str = run_id or uuid.uuid4().hex[:8]
        self.initial_context = initial_context
        self.max_cost_usd = max_cost_usd
        self.cumulative_cost_usd: float = 0.0

        # If using default PlannerAgent, pass config for LLM-based planning
        if self._config is not None and isinstance(self.planner, PlannerAgent):
            self.planner._set_config(self._config)

        self._all_results: list[SubAgentResult] = []
        self._running_tasks: list[asyncio.Task[Any]] = []
        self._start_time: float | None = None

    def cancel(self) -> None:
        """Explicitly cancel in-flight tasks in this loop run."""
        for task in list(self._running_tasks):
            if not task.done():
                task.cancel()

    async def run(self) -> AsyncIterator[LoopEvent]:
        """Drive the loop and yield LoopEvent objects.

        Usage::

            async for event in loop.run():
                if isinstance(event, FinishEvent):
                    print("Done:", event.summary)
        """
        async for event in self._run_impl():
            yield event

    async def _run_impl(self) -> AsyncIterator[LoopEvent]:
        """Internal generator-based implementation."""
        current_plan: list[dict[str, Any]] = []
        needs_plan = True
        is_replan = False
        outcome = None
        self._start_time = time.monotonic()

        try:
            for iteration in range(1, self.max_iterations + 1):
                logger.debug(
                    "AgentLoop [%s]: iteration %d / %d",
                    self.run_id,
                    iteration,
                    self.max_iterations,
                )

                # ── PLAN ────────────────────────────────────────────────
                if needs_plan:
                    try:
                        current_plan = await self._plan(current_plan if is_replan else None)
                    except Exception as exc:  # noqa: BLE001
                        yield LoopErrorEvent(
                            iteration=iteration,
                            run_id=self.run_id,
                            error=f"Planning failed: {exc}",
                        )
                        return

                    yield PlanEvent(
                        iteration=iteration,
                        run_id=self.run_id,
                        plan=current_plan,
                        is_replan=is_replan,
                    )

                    if not current_plan:
                        yield LoopErrorEvent(
                            iteration=iteration,
                            run_id=self.run_id,
                            error="Planner returned an empty plan.",
                        )
                        return

                # ── ACT ─────────────────────────────────────────────────
                step_results: list[SubAgentResult] = []
                for step_index, step in enumerate(current_plan):
                    agent_type = step.get("agent_type", SubAgentType.CODE_ANALYZER.value)
                    payload = dict(step.get("payload", {}))

                    yield StepStartEvent(
                        iteration=iteration,
                        run_id=self.run_id,
                        step_index=step_index,
                        agent_type=agent_type,
                        payload=payload,
                    )

                    t0 = time.monotonic()
                    result = await self._execute_step(agent_type, payload, iteration)
                    step_duration = time.monotonic() - t0
                    step_results.append(result)
                    self._all_results.append(result)

                    # Accumulate estimated step cost if model specified
                    model_used = payload.get("model", "")
                    if model_used:
                        from ..memory.budget import calculate_cost, estimate_tokens

                        prompt_tok = estimate_tokens(str(payload))
                        comp_tok = estimate_tokens(str(result.output or ""))
                        self.cumulative_cost_usd += calculate_cost(model_used, prompt_tok, comp_tok)

                    logger.info(
                        "AgentLoop [%s] step %d (%s) finished in %.3fs (success=%s)",
                        self.run_id,
                        step_index + 1,
                        agent_type,
                        step_duration,
                        result.success,
                    )

                    yield StepResultEvent(
                        iteration=iteration,
                        run_id=self.run_id,
                        step_index=step_index,
                        result=result,
                        duration_seconds=round(step_duration, 4),
                    )

                    if (
                        self.max_cost_usd is not None
                        and self.cumulative_cost_usd >= self.max_cost_usd
                    ):
                        yield LoopErrorEvent(
                            iteration=iteration,
                            run_id=self.run_id,
                            error=f"Budget limit of ${self.max_cost_usd:.4f} reached (total spent: ${self.cumulative_cost_usd:.4f})",
                        )
                        return

                # ── REFLECT ─────────────────────────────────────────────
                outcome = self.reflector.reflect(self.goal, current_plan, step_results)

                yield ReflectEvent(
                    iteration=iteration,
                    run_id=self.run_id,
                    decision=outcome.decision.value,
                    reason=outcome.reason,
                )

                if outcome.decision == ReflectDecision.FINISH:
                    total_duration = (
                        time.monotonic() - self._start_time if self._start_time else 0.0
                    )
                    summary = self._build_summary(step_results)
                    output = self._extract_finish_output(step_results)
                    logger.info(
                        "AgentLoop [%s] finished in %.3fs: %s",
                        self.run_id,
                        total_duration,
                        summary,
                    )
                    yield FinishEvent(
                        iteration=iteration,
                        run_id=self.run_id,
                        summary=summary,
                        output=output,
                        duration_seconds=round(total_duration, 4),
                    )
                    return

                if outcome.decision == ReflectDecision.FAIL:
                    yield LoopErrorEvent(
                        iteration=iteration,
                        run_id=self.run_id,
                        error=f"Reflector declared failure: {outcome.reason}",
                    )
                    return

                # RETRY or REPLAN — loop again.
                if outcome.decision == ReflectDecision.REPLAN:
                    needs_plan = True
                    is_replan = True
                elif outcome.decision == ReflectDecision.RETRY:
                    needs_plan = False
                    is_replan = False

            # Exhausted max_iterations without finishing.
            last_reason = outcome.reason if outcome else "No reflection outcome"
            raise LoopIterationLimitError(
                f"AgentLoop [{self.run_id}] reached max_iterations={self.max_iterations} "
                f"without finishing. Last reflect reason: {last_reason}"
            )

        finally:
            await self._cancel_running_tasks()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _plan(self, previous_plan: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Invoke Planner and return the plan step list."""
        payload: dict[str, Any] = {"query": self.goal}

        if self.plan_model:
            payload["model"] = self.plan_model
        elif self.router is not None:
            decision = self.router.decide("reasoning") or self.router.decide("chat")
            if decision is not None:
                payload["model"] = decision.primary
                payload["models"] = decision.models

        context_parts: list[str] = []
        if self.initial_context:
            context_parts.append(self.initial_context)

        if previous_plan:
            # Give the planner context about what was already tried.
            context_parts.append(
                f"Previous plan had {len(previous_plan)} step(s). Re-planning because "
                "some steps failed or the goal was not met."
            )

        if context_parts:
            payload["context"] = "\n\n".join(context_parts)

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
        """Execute a single step via the Executor registry.

        Passes routing fallback decision if payload does not have an explicit model.
        Handles OpenRouterError or task exceptions cleanly.
        """
        if "model" not in payload and self.router is not None:
            decision = self.router.decide("code") or self.router.decide("chat")
            if decision is not None:
                payload["model"] = decision.primary
                payload["models"] = decision.models

        try:
            coro = self.registry.execute(agent_type, payload)
            task: asyncio.Task[SubAgentResult] = asyncio.ensure_future(coro)
            self._running_tasks.append(task)
            try:
                result = await task
            finally:
                if task in self._running_tasks:
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
                agent_type=self._safe_subagent_type(agent_type),
                success=False,
                error=f"Model error: {exc}",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            return SubAgentResult(
                task_id="loop-error",
                agent_type=self._safe_subagent_type(agent_type),
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
    def _safe_subagent_type(agent_type: str) -> SubAgentType:
        try:
            return SubAgentType(agent_type)
        except ValueError:
            return SubAgentType.CODE_ANALYZER

    @staticmethod
    def _build_summary(results: list[SubAgentResult]) -> str:
        successes = sum(1 for r in results if r.success)
        return f"{successes}/{len(results)} step(s) completed successfully."

    @staticmethod
    def _extract_finish_output(results: list[SubAgentResult]) -> str | None:
        """Extract user-facing output from the completed steps."""
        extracted: list[str] = []
        for r in results:
            if not r.success or r.output is None:
                continue
            if isinstance(r.output, str) and r.output.strip():
                extracted.append(r.output.strip())
            elif isinstance(r.output, dict):
                # Common sub-agent output fields
                for key in ("summary", "content", "output", "analysis", "result"):
                    val = r.output.get(key)
                    if isinstance(val, str) and val.strip():
                        extracted.append(val.strip())
                        break
        return "\n\n".join(extracted) if extracted else None


# ------------------------------------------------------------------
# Task-intent heuristic (used by AgentSession / cli.py)
# ------------------------------------------------------------------

_AGENTIC_KEYWORDS: tuple[str, ...] = (
    "and then",
    "after that",
    "next,",
    "first,",
    "second,",
    "finally,",
    "run and",
    "analyze and",
    "analyze it",
    "analyze the",
    "list and",
    "write and",
    "create and then",
    "also read",
    "also check",
    "also list",
    "also run",
    "execute the",
    "execute command",
    "do the following",
    "read and",
    "read then",
    "and analyze",
    "then analyze",
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
    return (
        any(kw in lower for kw in _AGENTIC_KEYWORDS)
        or bool(re.search(r"\bstep\s*\d+\b", lower))
        or bool(re.search(r"\b1[.)]\s+.*?\b2[.)]\s+", lower, re.DOTALL))
        or bool(re.search(r"\bfirst\b.*?\bthen\b", lower, re.DOTALL))
    )


__all__ = ["AgentLoop", "LoopIterationLimitError", "is_agentic_task"]
