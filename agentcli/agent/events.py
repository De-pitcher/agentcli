"""Structured lifecycle events emitted by AgentLoop.

The loop yields these dataclasses as it progresses through each
Plan → Act → Reflect iteration.  cli.py uses them to render progress
under --verbose without a separate visibility flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..subagents.base import SubAgentResult


@dataclass
class LoopEvent:
    """Base class for all loop lifecycle events."""

    iteration: int = 0
    run_id: str = ""


@dataclass
class PlanEvent(LoopEvent):
    """Emitted when a plan has been produced (or re-produced)."""

    plan: list[dict[str, Any]] = field(default_factory=list)
    is_replan: bool = False


@dataclass
class StepStartEvent(LoopEvent):
    """Emitted just before a step is dispatched to a tool."""

    step_index: int = 0
    agent_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResultEvent(LoopEvent):
    """Emitted after a step completes (success or failure)."""

    step_index: int = 0
    result: SubAgentResult | None = None
    duration_seconds: float = 0.0


@dataclass
class ReflectEvent(LoopEvent):
    """Emitted after the reflector has evaluated this iteration."""

    decision: str = ""  # 'FINISH' | 'RETRY' | 'REPLAN' | 'FAIL'
    reason: str = ""


@dataclass
class FinishEvent(LoopEvent):
    """Emitted when the loop terminates (success path)."""

    summary: str = ""
    output: Any = None
    duration_seconds: float = 0.0


@dataclass
class LoopErrorEvent(LoopEvent):
    """Emitted when the loop terminates due to an unrecoverable error."""

    error: str = ""


@dataclass
class DriftDetectedEvent(LoopEvent):
    """Emitted when plan drift, oscillation, or action cycles are detected."""

    drift_score: float = 0.0
    severity: str = "low"  # 'low' | 'moderate' | 'critical'
    is_loop_detected: bool = False
    cycle_signature: str | None = None
    reasons: list[str] = field(default_factory=list)


@dataclass
class AutoHealingRollbackEvent(LoopEvent):
    """Emitted when auto-healing reverts filesystem changes after failures/drift."""

    snapshot_id: str = ""
    trigger: str = ""  # 'critical_drift' | 'consecutive_errors' | 'cycle_detected'
    reverted_files: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class StrategyRecoveryEvent(LoopEvent):
    """Emitted when a strategy recovery prompt is synthesized for plan correction."""

    strategy_prompt: str = ""
    alternative_actions: list[str] = field(default_factory=list)
    diagnostics: str = ""


__all__ = [
    "AutoHealingRollbackEvent",
    "DriftDetectedEvent",
    "FinishEvent",
    "LoopErrorEvent",
    "LoopEvent",
    "PlanEvent",
    "ReflectEvent",
    "StepResultEvent",
    "StepStartEvent",
    "StrategyRecoveryEvent",
]

