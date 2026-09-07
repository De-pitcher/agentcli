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

from .drift_detector import (
    ActionRecord,
    DriftReport,
    DriftSeverity,
    PlanDriftDetector,
)
from .events import (
    AutoHealingRollbackEvent,
    DriftDetectedEvent,
    FinishEvent,
    LoopErrorEvent,
    LoopEvent,
    PlanEvent,
    ReflectEvent,
    StepResultEvent,
    StepStartEvent,
    StrategyRecoveryEvent,
)
from .loop import AgentLoop, LoopIterationLimitError
from .reflector import DefaultReflector, ReflectDecision
from .registry import ToolRegistry
from .rollback import AutoHealingManager, HealingSnapshot

__all__ = [
    "ActionRecord",
    "AgentLoop",
    "AutoHealingManager",
    "AutoHealingRollbackEvent",
    "DefaultReflector",
    "DriftDetectedEvent",
    "DriftReport",
    "DriftSeverity",
    "FinishEvent",
    "HealingSnapshot",
    "LoopErrorEvent",
    "LoopEvent",
    "LoopIterationLimitError",
    "PlanDriftDetector",
    "PlanEvent",
    "ReflectDecision",
    "ReflectEvent",
    "StepResultEvent",
    "StepStartEvent",
    "StrategyRecoveryEvent",
    "ToolRegistry",
]

