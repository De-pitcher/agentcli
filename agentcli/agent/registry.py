"""Tool registry — uniform interface over Phase 3 sub-agents.

The registry is the single extension point for adding new tools to
the agent loop.  Phase 7's ecosystem work will register external tools
here without touching the loop engine.

Contract (stable across phases):
  - Register a coroutine factory with ``register(agent_type_str, factory)``.
  - The loop calls ``execute(agent_type_str, payload)`` and receives a
    ``SubAgentResult``; it never touches sub-agent internals directly.
  - ``execute`` must never raise; errors are returned in the result.

Default tools (registered automatically):
  code_analyzer, file_ops, shell_execution, web_search
"""

from __future__ import annotations

import logging
from typing import Any

from ..subagents.base import SubAgentResult, SubAgentTask, SubAgentType
from ..subagents.code_analyzer import CodeAnalyzerAgent
from ..subagents.file_ops import FileOpsAgent
from ..subagents.shell import ShellExecutionAgent
from ..subagents.web_search import WebSearchAgent

logger = logging.getLogger(__name__)

# Type alias for a tool factory callable.
_ToolFactory = Any  # Callable[[], SubAgent] — kept as Any for mypy simplicity


class ToolRegistry:
    """Maps agent-type strings to Phase 3 sub-agent factories.

    Usage::

        registry = ToolRegistry()
        result = await registry.execute("file_ops", {"operation": "list", "path": "."})

    Extension (Phase 7)::

        registry.register("my_tool", lambda: MyCustomAgent())
        result = await registry.execute("my_tool", {...})
    """

    def __init__(self) -> None:
        self._factories: dict[str, _ToolFactory] = {}
        self._register_defaults()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, agent_type: str, factory: _ToolFactory) -> None:
        """Register a tool factory.

        Args:
            agent_type: String key matching a SubAgentType value (or a
                        custom string for future tools).
            factory:    Zero-argument callable returning a SubAgent instance.
        """
        self._factories[agent_type] = factory
        logger.debug("ToolRegistry: registered tool '%s'", agent_type)

    def registered_types(self) -> list[str]:
        """Return the list of currently registered agent-type strings."""
        return list(self._factories.keys())

    async def execute(
        self,
        agent_type: str,
        payload: dict[str, Any],
    ) -> SubAgentResult:
        """Instantiate the tool and run the payload.

        Never raises.  Errors are captured and returned in the result.
        """
        factory = self._factories.get(agent_type)
        if factory is None:
            return SubAgentResult(
                task_id="unknown",
                agent_type=self._safe_type(agent_type),
                success=False,
                error=f"No tool registered for agent_type='{agent_type}'.",
            )

        task = SubAgentTask(
            agent_type=self._safe_type(agent_type),
            payload=payload,
        )
        try:
            agent = factory()
            result: SubAgentResult = await agent.run(task)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("ToolRegistry: tool '%s' raised unexpectedly: %s", agent_type, exc)
            return SubAgentResult(
                task_id=task.id,
                agent_type=self._safe_type(agent_type),
                success=False,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _register_defaults(self) -> None:
        self.register(SubAgentType.FILE_OPS.value, FileOpsAgent)
        self.register(SubAgentType.SHELL_EXECUTION.value, ShellExecutionAgent)
        self.register(SubAgentType.CODE_ANALYZER.value, CodeAnalyzerAgent)
        self.register(SubAgentType.WEB_SEARCH.value, WebSearchAgent)

    @staticmethod
    def _safe_type(agent_type: str) -> SubAgentType:
        try:
            return SubAgentType(agent_type)
        except ValueError:
            return SubAgentType.CODE_ANALYZER  # safe fallback for unknown types


__all__ = ["ToolRegistry"]
