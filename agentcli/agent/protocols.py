"""Protocol definitions for the agentcli agent loop.

Each stage of the Plan → Act → Reflect loop is expressed as a Python
Protocol so that concrete implementations can be swapped in tests or
by future contributors without modifying the loop engine itself.

Extension note (Phase 7):
  To add a new planner strategy, implement PlannerProtocol and pass it
  to AgentLoop(planner=...).  Same pattern for executor and reflector.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..subagents.base import SubAgentResult, SubAgentTask


@runtime_checkable
class PlannerProtocol(Protocol):
    """Decomposes a goal into an ordered list of SubAgentTask descriptors."""

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Return a SubAgentResult whose ``output["plan"]`` is a list of
        step dicts, each with at least ``agent_type`` and ``payload`` keys.
        May optionally include ``goal_criterion`` (str) per step so the
        reflector can evaluate success.
        """
        ...


@runtime_checkable
class ExecutorProtocol(Protocol):
    """Dispatches a single tool invocation and returns its result."""

    async def execute(
        self,
        agent_type: str,
        payload: dict[str, Any],
    ) -> SubAgentResult:
        """Run the tool identified by *agent_type* with *payload* and
        return the result.  Must never raise; errors surface in the
        ``SubAgentResult.success`` / ``error`` fields.
        """
        ...


from .reflector import ReflectDecision, ReflectOutcome


@runtime_checkable
class ReflectorProtocol(Protocol):
    """Critiques completed step results and decides the next loop action."""

    def reflect(
        self,
        goal: str,
        plan: list[dict[str, Any]],
        results: list[SubAgentResult],
    ) -> ReflectOutcome:
        """Return a ReflectOutcome describing what the loop should do next."""
        ...


__all__ = [
    "ExecutorProtocol",
    "PlannerProtocol",
    "ReflectDecision",
    "ReflectOutcome",
    "ReflectorProtocol",
]
