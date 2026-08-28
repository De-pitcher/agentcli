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

import importlib.util
import inspect
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..subagents.base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType
from ..subagents.code_analyzer import CodeAnalyzerAgent
from ..subagents.file_ops import FileOpsAgent
from ..subagents.shell import ShellExecutionAgent
from ..subagents.web_search import WebSearchAgent

logger = logging.getLogger(__name__)

# Type alias for a tool factory callable.
_ToolFactory = Any  # Callable[[], SubAgent] — kept as Any for mypy simplicity


class CallableAdapterAgent(SubAgent):
    """Adapter allowing standard Python callables to act as SubAgents."""

    def __init__(self, func: Callable[..., Any], name: str, description: str = "") -> None:
        super().__init__(agent_type=SubAgentType.CODE_ANALYZER)
        self._func = func
        self._name = name
        self._description = description

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        payload = task.payload
        try:
            if inspect.iscoroutinefunction(self._func):
                val = await self._func(**payload)
            else:
                val = self._func(**payload)
            return SubAgentResult(
                task_id=task.id,
                agent_type=task.agent_type,
                success=True,
                output=val if isinstance(val, dict) else {"output": str(val)},
            )
        except Exception as exc:  # noqa: BLE001
            return SubAgentResult(
                task_id=task.id,
                agent_type=task.agent_type,
                success=False,
                error=str(exc),
            )


class ToolRegistry:
    """Maps agent-type strings to Phase 3 sub-agent factories.

    Usage::

        registry = ToolRegistry()
        result = await registry.execute("file_ops", {"operation": "list", "path": "."})

    Extension (Phase 7)::

        registry.register("my_tool", lambda: MyCustomAgent())
        registry.register_callable("calculator", lambda a, b: a + b)
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

    def register_callable(
        self,
        name: str,
        func: Callable[..., Any],
        description: str = "",
    ) -> None:
        """Register a plain Python function as a tool."""
        self.register(name, lambda: CallableAdapterAgent(func, name=name, description=description))

    def load_plugin_file(self, path: str | Path) -> None:
        """Load a Python plugin file and register tools defined within it."""
        p = Path(path).resolve()
        if not p.is_file():
            logger.warning("Plugin file not found: %s", p)
            return

        module_name = f"agentcli_plugin_{p.stem}"
        spec = importlib.util.spec_from_file_location(module_name, p)
        if spec is None or spec.loader is None:
            logger.warning("Could not load spec for plugin %s", p)
            return

        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to execute plugin %s: %s", p, exc)
            return

        if hasattr(mod, "register_tools") and callable(mod.register_tools):
            mod.register_tools(self)
        elif hasattr(mod, "setup") and callable(mod.setup):
            mod.setup(self)
        logger.info("Loaded plugin from %s", p)

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


__all__ = ["CallableAdapterAgent", "ToolRegistry"]
