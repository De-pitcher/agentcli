"""agentcli.agent — Plan → Act → Reflect agentic loop core.

Phase 4 introduces a lightweight, plugin-style agent loop built
entirely in-process.  No external plugin-loading machinery is used;
swappability is achieved through Python Protocols and dependency
injection.

Relationship to Phase 3:
  Phase 3's PlannerAgent produces the initial task decomposition.
  Phase 4's AgentLoop wraps that decomposition with an act/reflect
  cycle and can re-invoke the Planner for re-planning when a step
  fails or the reflector decides more work is needed.

Public surface:
  AgentLoop          — the main loop engine
  ToolRegistry       — wraps Phase 3 sub-agents behind a uniform interface
  DefaultReflector   — heuristic reflect/critique implementation
  LoopEvent          — base class for all loop lifecycle events
  LoopIterationLimitError — raised when max_iterations is exceeded
"""

from .events import (
    FinishEvent,
    LoopErrorEvent,
    LoopEvent,
    PlanEvent,
    ReflectEvent,
    StepResultEvent,
    StepStartEvent,
)
from .loop import AgentLoop, LoopIterationLimitError
from .reflector import DefaultReflector, ReflectDecision
from .registry import ToolRegistry

__all__ = [
    "AgentLoop",
    "DefaultReflector",
    "FinishEvent",
    "LoopErrorEvent",
    "LoopEvent",
    "LoopIterationLimitError",
    "PlanEvent",
    "ReflectDecision",
    "ReflectEvent",
    "StepResultEvent",
    "StepStartEvent",
    "ToolRegistry",
]
