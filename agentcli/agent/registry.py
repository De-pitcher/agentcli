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
from ..subagents.clarification import ClarificationAgent
from ..subagents.code_analyzer import CodeAnalyzerAgent
from ..subagents.consensus import ConsensusAgent
from ..subagents.diagnostics import DiagnosticsAgent
from ..subagents.file_ops import FileOpsAgent
from ..subagents.grep_search import GrepSearchAgent
from ..subagents.shell import ShellExecutionAgent
from ..subagents.skill_runner import SkillRunnerAgent
from ..subagents.task_manager import TaskManagerAgent
from ..subagents.web_fetch import WebFetchAgent
from ..subagents.web_search import WebSearchAgent
from ..subagents.workspace import WorkspaceAgent
from ..subagents.worktree import WorktreeAgent

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

    def __init__(
        self, tool_configs: dict[str, dict[str, Any]] | None = None, config: Any | None = None
    ) -> None:
        self._tool_configs = tool_configs or {}
        self._config = config
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

    def list_tools(self) -> list[str]:
        """Alias for registered_types to return available tool names."""
        return self.registered_types()

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
        file_cfg = dict(self._tool_configs.get(SubAgentType.FILE_OPS.value) or {})
        if (
            self._config
            and hasattr(self._config, "subagents")
            and hasattr(self._config.subagents, "allow_write")
        ):
            file_cfg.setdefault("allow_write", self._config.subagents.allow_write)
        shell_cfg = self._tool_configs.get(SubAgentType.SHELL_EXECUTION.value)
        code_cfg = self._tool_configs.get(SubAgentType.CODE_ANALYZER.value)
        web_cfg = self._tool_configs.get(SubAgentType.WEB_SEARCH.value)

        self.register(SubAgentType.FILE_OPS.value, lambda: FileOpsAgent(config=file_cfg))
        self.register(
            SubAgentType.SHELL_EXECUTION.value, lambda: ShellExecutionAgent(config=shell_cfg)
        )
        code_analyzer = CodeAnalyzerAgent(config=code_cfg)
        if self._config:
            code_analyzer._set_config(self._config)
        self.register(SubAgentType.CODE_ANALYZER.value, lambda: code_analyzer)
        self.register(SubAgentType.WEB_SEARCH.value, lambda: WebSearchAgent(config=web_cfg))
        web_fetch_cfg = self._tool_configs.get(SubAgentType.WEB_FETCH.value)
        self.register(SubAgentType.WEB_FETCH.value, lambda: WebFetchAgent(config=web_fetch_cfg))
        grep_cfg = self._tool_configs.get(SubAgentType.GREP_SEARCH.value)
        self.register(SubAgentType.GREP_SEARCH.value, lambda: GrepSearchAgent(config=grep_cfg))
        ws_cfg = self._tool_configs.get(SubAgentType.WORKSPACE.value)
        self.register(SubAgentType.WORKSPACE.value, lambda: WorkspaceAgent(config=ws_cfg))
        consensus_cfg = self._tool_configs.get(SubAgentType.CONSENSUS.value)
        self.register(SubAgentType.CONSENSUS.value, lambda: ConsensusAgent(config=consensus_cfg))
        task_cfg = self._tool_configs.get(SubAgentType.TASK_MANAGER.value)
        self.register(SubAgentType.TASK_MANAGER.value, lambda: TaskManagerAgent(config=task_cfg))
        ask_cfg = self._tool_configs.get(SubAgentType.ASK_QUESTION.value)
        self.register(SubAgentType.ASK_QUESTION.value, lambda: ClarificationAgent(config=ask_cfg))
        diag_cfg = self._tool_configs.get(SubAgentType.DIAGNOSTICS_CHECK.value)
        self.register(
            SubAgentType.DIAGNOSTICS_CHECK.value, lambda: DiagnosticsAgent(config=diag_cfg)
        )
        self.register(
            SubAgentType.SKILL_RUNNER.value, lambda: SkillRunnerAgent()
        )
        self.register(
            SubAgentType.WORKTREE.value, lambda: WorktreeAgent()
        )


    @staticmethod
    def _safe_type(agent_type: str) -> SubAgentType:
        try:
            return SubAgentType(agent_type)
        except ValueError:
            return SubAgentType.CODE_ANALYZER  # safe fallback for unknown types


__all__ = ["CallableAdapterAgent", "ToolRegistry"]
