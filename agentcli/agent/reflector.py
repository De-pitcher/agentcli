"""Reflect/critique stage for the agentcli agent loop.

The reflector evaluates completed step results against the original
goal and decides what the loop should do next.  It is intentionally
pure (no I/O, no async) so it can be unit-tested without any mocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..subagents.base import SubAgentResult


class ReflectDecision(str, Enum):
    """Action the loop should take after reflection."""

    FINISH = "FINISH"  # Goal met — surface results to user.
    RETRY = "RETRY"  # Transient failure — retry the same plan.
    REPLAN = "REPLAN"  # Unmet or blocked — ask Planner for a new plan.
    FAIL = "FAIL"  # Unrecoverable — surface error to user.


@dataclass
class ReflectOutcome:
    """Full reflection verdict including the decision and a human-readable reason."""

    decision: ReflectDecision
    reason: str


class DefaultReflector:
    """Heuristic reflector for the Plan → Act → Reflect loop.

    Evaluation order:
      1. If every step succeeded and optional goal_criterion strings are
         present in the step outputs, decision is FINISH.
      2. If all steps failed with transient-looking errors (rate-limit,
         timeout), decision is RETRY.
      3. If some steps failed with hard errors (file not found, permission
         denied, command not found), decision is REPLAN so the Planner
         can route around the failure.
      4. If *no* steps produced any output at all, decision is FAIL.
    """

    # Keywords that suggest a transient / retriable failure.
    _TRANSIENT_KEYWORDS: tuple[str, ...] = (
        "rate limit",
        "rate-limit",
        "too many requests",
        "timeout",
        "timed out",
        "temporarily unavailable",
        "503",
        "429",
    )

    # Keywords that suggest a hard failure that re-planning may fix.
    _HARD_FAILURE_KEYWORDS: tuple[str, ...] = (
        "not found",
        "no such file",
        "permission denied",
        "command not found",
        "no module named",
        "syntax error",
        "import error",
    )

    def reflect(
        self,
        goal: str,
        plan: list[dict[str, Any]],
        results: list[SubAgentResult],
    ) -> ReflectOutcome:
        """Evaluate results and return a ReflectOutcome.

        Args:
            goal:    The original user goal string.
            plan:    The list of plan step dicts (may contain
                     ``goal_criterion`` per step).
            results: Completed SubAgentResult objects in step order.
        """
        if not results:
            return ReflectOutcome(
                decision=ReflectDecision.FAIL,
                reason="No step results were produced.",
            )

        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]

        # --- All steps succeeded ---
        if not failures:
            unmet = self._check_goal_criteria(plan, results)
            if unmet:
                return ReflectOutcome(
                    decision=ReflectDecision.REPLAN,
                    reason=f"Steps succeeded but goal criterion unmet: {unmet}",
                )
            return ReflectOutcome(
                decision=ReflectDecision.FINISH,
                reason=f"All {len(successes)} step(s) completed successfully.",
            )

        # --- Classify the failures ---
        transient_failures = [r for r in failures if self._is_transient(r.error or "")]
        hard_failures = [r for r in failures if not self._is_transient(r.error or "")]

        if hard_failures:
            failed_types = ", ".join(r.agent_type.value for r in hard_failures)
            return ReflectOutcome(
                decision=ReflectDecision.REPLAN,
                reason=(
                    f"{len(hard_failures)} step(s) failed with hard errors "
                    f"({failed_types}) — requesting re-plan."
                ),
            )

        if transient_failures and not successes:
            return ReflectOutcome(
                decision=ReflectDecision.RETRY,
                reason=(
                    f"All {len(transient_failures)} step(s) hit transient errors "
                    "(rate-limit / timeout) — will retry."
                ),
            )

        # Partial success + transient failures: try re-planning for the failed ones.
        return ReflectOutcome(
            decision=ReflectDecision.REPLAN,
            reason=(
                f"{len(successes)} step(s) succeeded, "
                f"{len(transient_failures)} hit transient errors — re-planning."
            ),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _is_transient(self, error: str) -> bool:
        err_lower = error.lower()
        return any(kw in err_lower for kw in self._TRANSIENT_KEYWORDS)

    def _check_goal_criteria(
        self,
        plan: list[dict[str, Any]],
        results: list[SubAgentResult],
    ) -> str:
        """Return a non-empty string describing the first unmet criterion,
        or an empty string if all criteria are satisfied.
        """
        for step, result in zip(plan, results):
            criterion: str = step.get("goal_criterion", "")
            if not criterion:
                continue
            output_str = str(result.output or "").lower()
            if criterion.lower() not in output_str:
                return criterion
        return ""


__all__ = ["DefaultReflector", "ReflectDecision", "ReflectOutcome"]
