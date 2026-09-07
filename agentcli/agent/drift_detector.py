"""Plan Drift & Action Cycle Detector (Phase 36).

Tracks execution history, measures drift against planned goals, identifies
oscillating actions (loops/ping-pong), and calculates drift severity scores.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class DriftSeverity(str, Enum):
    """Drift severity levels."""

    LOW = "low"
    MODERATE = "moderate"
    CRITICAL = "critical"


@dataclass
class ActionRecord:
    """Record of a single executed action or subagent step."""

    iteration: int
    step_index: int
    agent_type: str
    payload: dict[str, Any]
    success: bool
    error: str | None = None
    timestamp: float = field(default_factory=time.time)
    action_signature: str = ""

    def __post_init__(self) -> None:
        if not self.action_signature:
            self.action_signature = self.compute_signature(self.agent_type, self.payload)

    @staticmethod
    def compute_signature(agent_type: str, payload: dict[str, Any]) -> str:
        """Derive a canonical normalized signature for an action."""
        # Extract salient keys
        salient: dict[str, Any] = {
            "type": agent_type,
            "cmd": payload.get("command") or payload.get("cmd") or "",
            "path": payload.get("path") or payload.get("file") or payload.get("target") or "",
            "op": payload.get("operation") or payload.get("op") or "",
            "query": payload.get("query") or payload.get("pattern") or "",
        }
        # Strip empty fields
        filtered = {k: v for k, v in salient.items() if v}
        serialized = json.dumps(filtered, sort_keys=True)
        h = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]
        readable = f"{agent_type}:{salient.get('op') or salient.get('cmd') or salient.get('path') or salient.get('query') or 'action'}"
        return f"{readable}#{h}"


@dataclass
class DriftReport:
    """Diagnostic report describing current plan drift and cycle status."""

    drift_score: float  # 0.0 (aligned) to 1.0 (severely drifted/stuck)
    severity: DriftSeverity
    is_loop_detected: bool
    cycle_signature: str | None
    reasons: list[str] = field(default_factory=list)
    consecutive_failures: int = 0
    unique_actions_count: int = 0
    total_actions_count: int = 0


class PlanDriftDetector:
    """Detects loops, oscillating edits, repeated errors, and execution drift."""

    def __init__(
        self,
        loop_threshold: int = 2,
        max_drift_score: float = 0.7,
    ) -> None:
        self.loop_threshold = loop_threshold
        self.max_drift_score = max_drift_score
        self._history: list[ActionRecord] = []
        self._consecutive_failures: int = 0

    def record_action(
        self,
        iteration: int,
        step_index: int,
        agent_type: str,
        payload: dict[str, Any],
        success: bool,
        error: str | None = None,
    ) -> ActionRecord:
        """Record an executed step and update failure counters."""
        rec = ActionRecord(
            iteration=iteration,
            step_index=step_index,
            agent_type=agent_type,
            payload=payload,
            success=success,
            error=error,
        )
        self._history.append(rec)
        if success:
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
        return rec

    def detect_cycles(self) -> tuple[bool, str | None, list[str]]:
        """Detect execution loops, repeated errors, or ping-pong oscillations.

        Returns:
            (is_loop_detected, cycle_signature, reasons)
        """
        reasons: list[str] = []
        if len(self._history) < 2:
            return False, None, []

        # 1. Consecutive identical failures on the same action signature
        if self._consecutive_failures >= self.loop_threshold:
            recent_failed = [r for r in self._history[-self._consecutive_failures :] if not r.success]
            if len(recent_failed) >= self.loop_threshold:
                sigs = [r.action_signature for r in recent_failed]
                if len(set(sigs)) == 1:
                    sig = sigs[0]
                    reasons.append(
                        f"Action '{sig}' failed {len(recent_failed)} consecutive times without recovery."
                    )
                    return True, sig, reasons

        # 2. Ping-pong oscillation: A -> B -> A -> B
        if len(self._history) >= 4:
            s1, s2, s3, s4 = [r.action_signature for r in self._history[-4:]]
            if s1 == s3 and s2 == s4 and s1 != s2:
                cycle_sig = f"{s1} <-> {s2}"
                reasons.append(f"Oscillating ping-pong execution detected between '{s1}' and '{s2}'.")
                return True, cycle_sig, reasons

        # 3. Repeated identical tool actions (>= 3 times in recent 5 actions)
        recent = self._history[-5:]
        if len(recent) >= 3:
            sig_counts: dict[str, int] = {}
            for r in recent:
                sig_counts[r.action_signature] = sig_counts.get(r.action_signature, 0) + 1
            for sig, count in sig_counts.items():
                if count >= 3:
                    reasons.append(
                        f"Repeated execution of action '{sig}' ({count} times in last {len(recent)} steps)."
                    )
                    return True, sig, reasons

        # 4. Same file modified repeatedly with errors
        recent_file_errors: list[str] = []
        for r in reversed(self._history[-6:]):
            if not r.success and r.payload.get("path"):
                recent_file_errors.append(str(r.payload["path"]))
        if len(recent_file_errors) >= 3:
            most_frequent_path = max(set(recent_file_errors), key=recent_file_errors.count)
            if recent_file_errors.count(most_frequent_path) >= 2:
                reasons.append(
                    f"Repeated mutation failures on file '{most_frequent_path}'."
                )
                return True, f"file:{most_frequent_path}", reasons

        return False, None, []

    def evaluate_drift(
        self,
        current_plan: list[dict[str, Any]] | None = None,
        planned_total_steps: int | None = None,
    ) -> DriftReport:
        """Calculate overall drift score and classify severity."""
        total = len(self._history)
        if total == 0:
            return DriftReport(
                drift_score=0.0,
                severity=DriftSeverity.LOW,
                is_loop_detected=False,
                cycle_signature=None,
                reasons=[],
                consecutive_failures=0,
                unique_actions_count=0,
                total_actions_count=0,
            )

        is_loop, cycle_sig, loop_reasons = self.detect_cycles()
        reasons = list(loop_reasons)

        failures = sum(1 for r in self._history if not r.success)
        failure_rate = failures / total

        unique_sigs = {r.action_signature for r in self._history}
        repetition_factor = 1.0 - (len(unique_sigs) / total)

        # Baseline score from failure rate and repetition
        score = (failure_rate * 0.5) + (repetition_factor * 0.3)

        # Consecutive failure penalty
        if self._consecutive_failures > 0:
            score += min(0.4, self._consecutive_failures * 0.15)
            if self._consecutive_failures >= 2:
                reasons.append(f"{self._consecutive_failures} consecutive step failures recorded.")

        # Cycle penalty
        if is_loop:
            score = max(score, 0.75)

        # Iteration inflation penalty (executing far more steps than planned)
        expected_steps = planned_total_steps or (len(current_plan) if current_plan else 5)
        if total > expected_steps * 2:
            inflation = min(0.3, (total - expected_steps * 2) * 0.05)
            score += inflation
            reasons.append(f"Step count ({total}) significantly exceeded expected plan budget ({expected_steps}).")

        score = min(1.0, max(0.0, round(score, 3)))

        # Classify severity
        if score >= self.max_drift_score or is_loop or self._consecutive_failures >= 3:
            severity = DriftSeverity.CRITICAL
        elif score >= 0.4 or self._consecutive_failures >= 2:
            severity = DriftSeverity.MODERATE
        else:
            severity = DriftSeverity.LOW

        return DriftReport(
            drift_score=score,
            severity=severity,
            is_loop_detected=is_loop,
            cycle_signature=cycle_sig,
            reasons=reasons,
            consecutive_failures=self._consecutive_failures,
            unique_actions_count=len(unique_sigs),
            total_actions_count=total,
        )

    def get_action_history(self) -> list[ActionRecord]:
        """Return shallow copy of recorded action history."""
        return list(self._history)

    def reset(self) -> None:
        """Clear recorded history and counters."""
        self._history.clear()
        self._consecutive_failures = 0


__all__ = [
    "ActionRecord",
    "DriftReport",
    "DriftSeverity",
    "PlanDriftDetector",
]
