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

import asyncio
import copy
import importlib.util
import inspect
import logging
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..contracts import ToolSpec, check_schema, validate
from ..policy import active_policy
from ..runtime import RunContext
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
from ..tools_schema import TOOL_DEFINITIONS
from ..usage import active_ledger

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
                error=str(exc) or "Tool timed out at its deadline",
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
        self,
        tool_configs: dict[str, dict[str, Any]] | None = None,
        config: Any | None = None,
        context: RunContext | None = None,
    ) -> None:
        self._tool_configs = tool_configs or {}
        self.context = context or RunContext(config=config)
        self._owns_context = context is None
        self._config = config
        self._factories: dict[str, _ToolFactory] = {}
        self.specs: dict[str, ToolSpec] = {}
        self._register_defaults()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        agent_type: str,
        factory: _ToolFactory,
        schema: dict[str, Any] | None = None,
        description: str = "",
        effect: str = "unknown",
    ) -> None:
        """Register a tool factory.

        Args:
            agent_type: String key matching a SubAgentType value (or a
                        custom string for future tools).
            factory:    Zero-argument callable returning a SubAgent instance.
        """
        if agent_type in self._factories:
            raise ValueError(f"Tool collision: {agent_type}")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", agent_type):
            raise ValueError("Invalid tool name")
        definition = TOOL_DEFINITIONS.get(agent_type, {}).get("function", {})
        contract = copy.deepcopy(
            schema
            if schema is not None
            else definition.get("parameters", {"type": "object", "properties": {}})
        )
        if agent_type in TOOL_DEFINITIONS:
            contract["additionalProperties"] = False
        check_schema(contract)
        if effect not in {"observation", "mutation", "unknown"}:
            raise ValueError("Invalid tool effect declaration")
        self.specs[agent_type] = ToolSpec(
            agent_type,
            description or definition.get("description", "Custom tool"),
            contract,
            effect,
            agent_type in TOOL_DEFINITIONS,
        )
        self._factories[agent_type] = factory
        logger.debug("ToolRegistry: registered tool '%s'", agent_type)

    def register_callable(
        self,
        name: str,
        func: Callable[..., Any],
        description: str = "",
        schema: dict[str, Any] | None = None,
        effect: str = "unknown",
    ) -> None:
        """Register a callable with an explicit or inferred signature contract."""
        if schema is None:
            properties = {}
            required = []
            for parameter in inspect.signature(func).parameters.values():
                if parameter.kind in {
                    parameter.VAR_POSITIONAL,
                    parameter.VAR_KEYWORD,
                    parameter.POSITIONAL_ONLY,
                }:
                    raise ValueError("Callable requires an explicit schema for this signature")
                kinds = {
                    str: "string",
                    int: "integer",
                    float: "number",
                    bool: "boolean",
                    dict: "object",
                    list: "array",
                }
                annotation = parameter.annotation
                if annotation not in kinds and parameter.default is not parameter.empty:
                    annotation = type(parameter.default)
                properties[parameter.name] = {
                    "type": kinds.get(
                        annotation, ["string", "number", "boolean", "object", "array", "null"]
                    )
                }
                if parameter.default is parameter.empty:
                    required.append(parameter.name)
            schema = {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            }
        self.register(
            name,
            lambda: CallableAdapterAgent(func, name=name, description=description),
            schema=schema,
            description=description,
            effect=effect,
        )

    def load_plugin_file(self, path: str | Path) -> None:
        """Load a Python plugin file and register tools defined within it."""
        from ..policy import active_policy

        # Collect applicable explicit authorities at load time:
        # active_policy.get() and self.context.policy
        active = active_policy.get()
        ctx_policy = self.context.policy

        applicable_policies = [p for p in (active, ctx_policy) if p is not None]

        # Explicit denies stay binding when authorities overlap:
        # If any applicable policy denies execution, deny immediately before file inspection/import.
        if not all(p.allow_execution for p in applicable_policies):
            raise PermissionError("Plugins are trusted Python code and require trusted execution")

        # Plugin loading must satisfy path containment under ALL applicable authorities before import side effects.
        # Resolve one canonical absolute plugin resource and enforce every applicable authority
        # on that SAME resource before any import side effect.
        # Use registry owner context policy first if present, otherwise active policy, otherwise local resolve.
        primary_policy = ctx_policy or active
        if primary_policy is not None:
            canonical_path = primary_policy.path(path)
        else:
            canonical_path = Path(path).resolve()

        # Enforce that ALL applicable authorities authorize this exact same canonical resource
        for pol in applicable_policies:
            pol.path(canonical_path)

        p = canonical_path
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
        token = active_policy.set(self.context.policy)
        usage_token = active_ledger.set(self.context.ledger)
        try:
            async with asyncio.timeout(self.context.policy.timeout_seconds):
                validate(payload, {"type": "object"})
                payload = self.specs[agent_type].canonical(payload)
                task = replace(task, payload=payload)
                validate(payload, self.specs[agent_type].schema)
                await self.context.policy.aauthorize(agent_type, payload)
                agent = factory()
                result: SubAgentResult = await agent.run(task)
                from .evidence import annotate

                annotate(
                    result,
                    agent_type,
                    self.specs[agent_type].operation(payload),
                    payload,
                    self.context.tasks,
                )
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("ToolRegistry: tool '%s' raised unexpectedly: %s", agent_type, exc)
            return SubAgentResult(
                task_id=task.id,
                agent_type=self._safe_type(agent_type),
                success=False,
                error=str(exc),
            )

        finally:
            active_policy.reset(token)
            active_ledger.reset(usage_token)

    async def aclose(self) -> None:
        if self._owns_context:
            await self.context.aclose()

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
        file_cfg["working_dir"] = str(self.context.policy.root)
        file_cfg["journal"] = self.context.journal
        file_cfg["allow_write"] = self.context.policy.allow_write
        file_cfg["read_only"] = not self.context.policy.allow_write
        shell_cfg = dict(self._tool_configs.get(SubAgentType.SHELL_EXECUTION.value) or {})
        shell_cfg["working_dir"] = str(self.context.policy.root)
        shell_cfg["allow_execution"] = self.context.policy.allow_execution
        code_cfg = self._tool_configs.get(SubAgentType.CODE_ANALYZER.value)
        web_cfg = self._tool_configs.get(SubAgentType.WEB_SEARCH.value)

        self.register(SubAgentType.FILE_OPS.value, lambda: FileOpsAgent(config=file_cfg))
        self.register(
            SubAgentType.SHELL_EXECUTION.value, lambda: ShellExecutionAgent(config=shell_cfg)
        )
        code_analyzer = CodeAnalyzerAgent(config=code_cfg)
        if self._config:
            code_analyzer._set_config(self._config)
            code_analyzer._client = self.context.client

        def analyzer_factory() -> CodeAnalyzerAgent:
            code_analyzer._client = self.context.client
            return code_analyzer

        self.register(SubAgentType.CODE_ANALYZER.value, analyzer_factory)
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
        self.register(
            SubAgentType.TASK_MANAGER.value,
            lambda: TaskManagerAgent(config=task_cfg, task_manager=self.context.tasks),
        )
        ask_cfg = self._tool_configs.get(SubAgentType.ASK_QUESTION.value)
        self.register(SubAgentType.ASK_QUESTION.value, lambda: ClarificationAgent(config=ask_cfg))
        diag_cfg = self._tool_configs.get(SubAgentType.DIAGNOSTICS_CHECK.value)
        self.register(
            SubAgentType.DIAGNOSTICS_CHECK.value, lambda: DiagnosticsAgent(config=diag_cfg)
        )
        self.register(
            SubAgentType.SKILL_RUNNER.value,
            lambda: SkillRunnerAgent(workspace_dir=str(self.context.policy.root)),
        )
        self.register(
            SubAgentType.WORKTREE.value,
            lambda: WorktreeAgent(
                manager=__import__(
                    "agentcli.worktree.manager", fromlist=["WorktreeManager"]
                ).WorktreeManager(
                    repo_root=self.context.policy.root,
                    policy=self.context.policy,
                    defer_validation=True,
                )
            ),
        )

    @staticmethod
    def _safe_type(agent_type: str) -> SubAgentType:
        try:
            return SubAgentType(agent_type)
        except ValueError:
            return SubAgentType.CODE_ANALYZER  # safe fallback for unknown types


__all__ = ["CallableAdapterAgent", "ToolRegistry"]
