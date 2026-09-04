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


__all__ = [
    "FinishEvent",
    "LoopErrorEvent",
    "LoopEvent",
    "PlanEvent",
    "ReflectEvent",
    "StepResultEvent",
    "StepStartEvent",
]
