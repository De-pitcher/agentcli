"""Planner sub-agent.

Decomposes complex tasks into sub-tasks for dispatch to other agents.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from ..config import Config
from ..openrouter_client import ChatMessage, OpenRouterClient, OpenRouterError
from ..parser import robust_json_loads
from .base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

if TYPE_CHECKING:
    from .bus import MessageBus


class PlannerAgent(SubAgent):
    """Sub-agent for task decomposition and planning.

    Analyzes a user request and breaks it down into sub-tasks
    that can be executed by other specialized agents.

    Phase 3 / Phase 4 relationship:
        Phase 3 introduced PlannerAgent as a standalone sub-agent that
        decomposes a task into a flat list of step dicts dispatched via
        SubAgentSpawner.

        Phase 4's AgentLoop reuses this class for its "plan" stage and
        can re-invoke it for re-planning when a step fails.  The loop
        wraps the decomposition with an act/reflect cycle; it does NOT
        duplicate this planning logic.

        Phase 4 extension: each step dict now optionally includes a
        ``goal_criterion`` key (str).  The DefaultReflector checks
        whether this string appears in the step's result output to
        verify the step actually achieved its intended outcome.
        Callers that do not use goal_criterion can safely ignore the
        new key — it defaults to an empty string.

    Phase 8 (MVP Truth & Safety): When a model is provided in the payload,
    the planner calls the LLM for structured planning instead of using
    keyword heuristics. The heuristic remains as a fallback when no model
    is specified.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        message_bus: MessageBus | None = None,
    ) -> None:
        super().__init__(SubAgentType.PLANNER, config, message_bus)
        self._client: OpenRouterClient | None = None
        self._config: Config | None = None

    async def _get_client(self, config: Config) -> OpenRouterClient:
        if self._client is None:
            self._client = OpenRouterClient(config.openrouter)
        return self._client

    def _set_config(self, config: Config) -> None:
        """Set the config for LLM-based planning."""
        self._config = config

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Decompose a task into sub-tasks.

        Expected payload:
            - query: user's request/question
            - context: optional additional context
            - available_agents: list of available agent types (optional)
            - model: optional model ID for LLM-based planning
            - models: optional list of model fallbacks for LLM-based planning

        Returns a plan with sub-tasks to execute.
        """
        payload = task.payload
        query = payload.get("query", "")
        context = payload.get("context", "")
        available_agents = payload.get(
            "available_agents",
            [
                SubAgentType.CODE_ANALYZER,
                SubAgentType.FILE_OPS,
                SubAgentType.SHELL_EXECUTION,
            ],
        )
        model = payload.get("model")
        models = payload.get("models")

        if not query:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error="No query provided for planning",
            )

        # Use LLM-based planning if model is provided and config is available
        if model and self._config:
            sub_tasks = await self._generate_plan_llm(
                query, context, available_agents, model, models
            )
        else:
            sub_tasks = self._generate_plan(query, context, available_agents)

        return SubAgentResult(
            task_id=task.id,
            agent_type=self.agent_type,
            success=True,
            output={
                "original_query": query,
                "context": context,
                "plan": sub_tasks,
                "total_subtasks": len(sub_tasks),
            },
        )

    def _generate_plan(
        self,
        query: str,
        context: str,
        available_agents: list[Any],
    ) -> list[dict[str, Any]]:
        """Generate a task plan using heuristic rules.

        Filters and validates tasks strictly against available_agents.
        """
        allowed_types: set[SubAgentType] = set()
        for a in available_agents:
            if isinstance(a, SubAgentType):
                allowed_types.add(a)
            elif isinstance(a, str):
                try:
                    allowed_types.add(SubAgentType(a))
                except ValueError:
                    pass

        raw_tasks: list[dict[str, Any]] = []
        query_lower = query.lower()

        # Code analysis tasks
        if any(
            keyword in query_lower
            for keyword in ["review", "analyze", "audit", "bug", "code", "refactor"]
        ):
            focus = "security" if "security" in query_lower else "general"
            # Use "done" as goal_criterion since mock outputs and real tools typically
            # include "done" or "completed" in their success responses
            criterion = "done"
            raw_tasks.append(
                {
                    "agent_type": SubAgentType.CODE_ANALYZER.value,
                    "payload": {
                        "files": self._extract_file_paths(query),
                        "focus": focus,
                        "context": f"User requested: {query}",
                    },
                    "priority": 10,
                    "goal_criterion": criterion,
                }
            )

        # File operations
        if any(
            keyword in query_lower
            for keyword in ["read", "write", "create", "delete", "list", "file"]
        ):
            files = self._extract_file_paths(query)
            operation = self._infer_file_operation(query)
            path = files[0] if files else ""
            # Set goal_criterion based on operation
            if operation == "read":
                criterion = path if path else "file content"
            elif operation == "write":
                criterion = "file created" if path else "file written"
            elif operation == "delete":
                criterion = "file deleted"
            elif operation == "list":
                criterion = "directory listed"
            else:
                criterion = operation
            raw_tasks.append(
                {
                    "agent_type": SubAgentType.FILE_OPS.value,
                    "payload": {
                        "operation": operation,
                        "path": path,
                    },
                    "priority": 5,
                    "goal_criterion": criterion,
                }
            )

        # Shell execution
        if any(
            keyword in query_lower
            for keyword in ["run", "execute", "run command", "shell", "terminal"]
        ):
            cmd = self._extract_command(query)
            if cmd:
                # Extract a meaningful criterion from the command
                if "test" in cmd.lower() or "pytest" in cmd.lower():
                    criterion = "test passed"
                elif "build" in cmd.lower() or "compile" in cmd.lower():
                    criterion = "build successful"
                elif "lint" in cmd.lower() or "format" in cmd.lower():
                    criterion = "lint passed"
                else:
                    criterion = "command completed"
                raw_tasks.append(
                    {
                        "agent_type": SubAgentType.SHELL_EXECUTION.value,
                        "payload": {
                            "command": cmd,
                            "timeout": 30.0,
                        },
                        "priority": 5,
                        "goal_criterion": criterion,
                    }
                )

        # Workspace operations
        if any(
            keyword in query_lower
            for keyword in [
                "git status",
                "branch",
                "search code",
                "search codebase",
                "find file",
                "search file",
                "file tree",
                "directory tree",
            ]
        ):
            if "git" in query_lower or "branch" in query_lower:
                op = "git_status"
                crit = "Branch:"
            elif "tree" in query_lower:
                op = "list_tree"
                crit = "root"
            elif "code" in query_lower or "symbol" in query_lower or "function" in query_lower:
                op = "search_code"
                crit = "query"
            else:
                op = "search_files"
                crit = "matches"

            raw_tasks.append(
                {
                    "agent_type": SubAgentType.WORKSPACE.value,
                    "payload": {
                        "operation": op,
                        "query": query,
                        "pattern": "*",
                    },
                    "priority": 8,
                    "goal_criterion": crit,
                }
            )

        # Default to code analyzer if no specific agent matched
        if not raw_tasks:
            # Set a general criterion based on query content
            if "explain" in query_lower or "how" in query_lower:
                criterion = "explanation"
            elif "write" in query_lower or "create" in query_lower:
                criterion = "code created"
            elif "fix" in query_lower or "debug" in query_lower:
                criterion = "fix applied"
            else:
                criterion = "analysis complete"
            raw_tasks.append(
                {
                    "agent_type": SubAgentType.CODE_ANALYZER.value,
                    "payload": {
                        "files": self._extract_file_paths(query),
                        "focus": "general",
                        "context": f"User asked: {query}",
                    },
                    "priority": 1,
                    "goal_criterion": criterion,
                }
            )

        # Filter strictly against allowed_types
        validated_tasks: list[dict[str, Any]] = []
        for task in raw_tasks:
            agent_type_enum = SubAgentType(task["agent_type"])
            if agent_type_enum in allowed_types:
                validated_tasks.append(task)
            elif (
                SubAgentType.CODE_ANALYZER in allowed_types
                and agent_type_enum != SubAgentType.CODE_ANALYZER
            ):
                # Fallback to code analyzer if primary agent is not available
                validated_tasks.append(
                    {
                        "agent_type": SubAgentType.CODE_ANALYZER.value,
                        "payload": {
                            "files": self._extract_file_paths(query),
                            "focus": "general",
                            "context": f"Fallback from {task['agent_type']} for: {query}",
                        },
                        "priority": 1,
                        "goal_criterion": "",
                    }
                )

        return validated_tasks

    async def _generate_plan_llm(
        self,
        query: str,
        context: str,
        available_agents: list[Any],
        model: str,
        models: list[str] | None,
    ) -> list[dict[str, Any]]:
        """Generate a task plan using LLM-based planning (native tool calling with legacy fallback)."""
        allowed_types: set[SubAgentType] = set()
        for a in available_agents:
            if isinstance(a, SubAgentType):
                allowed_types.add(a)
            elif isinstance(a, str):
                try:
                    allowed_types.add(SubAgentType(a))
                except ValueError:
                    pass

        if self._config is None:
            return self._generate_plan(query, context, available_agents)

        client = await self._get_client(self._config)

        # -------------------------------------------------------------
        # 1. Native Tool Calling Engine (Primary Path)
        # -------------------------------------------------------------
        try:
            from ..tools_schema import get_tool_definitions

            tools = get_tool_definitions(allowed_types)
            if tools:
                system_prompt = (
                    "You are an autonomous task planner. Call the required tools in order "
                    "to satisfy the user's request."
                )
                user_prompt = f"User request: {query}\n\nContext: {context}"
                messages = [
                    ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=user_prompt),
                ]
                completion = await client.chat_completion(
                    messages=messages,
                    model=model,
                    models=models,
                    tools=tools,
                )
                choices = completion.get("choices", [])
                if choices and isinstance(choices, list):
                    message = choices[0].get("message", {})
                    tool_calls = message.get("tool_calls", [])
                    if tool_calls and isinstance(tool_calls, list):
                        native_tasks: list[dict[str, Any]] = []
                        for call in tool_calls:
                            func = call.get("function", {})
                            name = func.get("name", "")
                            args_str = func.get("arguments", "{}")
                            try:
                                args = (
                                    robust_json_loads(args_str)
                                    if isinstance(args_str, str)
                                    else args_str
                                )
                            except (json.JSONDecodeError, ValueError, TypeError):
                                args = {}

                            try:
                                agent_type_enum = SubAgentType(name)
                            except ValueError:
                                continue

                            if agent_type_enum in allowed_types:
                                # Infer appropriate goal criterion for verification
                                if agent_type_enum == SubAgentType.FILE_OPS:
                                    criterion = args.get("path") or args.get("operation", "file")
                                elif agent_type_enum == SubAgentType.SHELL_EXECUTION:
                                    criterion = "completed"
                                elif agent_type_enum == SubAgentType.CODE_ANALYZER:
                                    criterion = "analysis"
                                elif agent_type_enum == SubAgentType.WEB_SEARCH:
                                    criterion = "search results"
                                elif agent_type_enum == SubAgentType.WORKSPACE:
                                    criterion = str(args.get("operation") or "workspace")
                                else:
                                    criterion = "done"

                                native_tasks.append(
                                    {
                                        "agent_type": agent_type_enum.value,
                                        "payload": args,
                                        "priority": 5,
                                        "goal_criterion": criterion,
                                    }
                                )
                        if native_tasks:
                            return native_tasks
        except (
            OpenRouterError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
            TypeError,
            AttributeError,
        ) as exc:
            self._logger.debug(
                "Native tool calling failed or not supported by model: %s; falling back to legacy prompt",
                exc,
            )

        # -------------------------------------------------------------
        # 2. Legacy Prompt-Based JSON Planning (Fallback Path)
        # -------------------------------------------------------------
        # Build agent descriptions for the prompt
        agent_descriptions = {
            SubAgentType.CODE_ANALYZER: "Analyze code files for bugs, security issues, performance problems, and style. Can read files provided via @path references.",
            SubAgentType.FILE_OPS: "Perform file operations: read, write, create, delete, list, mkdir. Paths are constrained to working directory.",
            SubAgentType.SHELL_EXECUTION: "Execute shell commands safely. Uses allowlist/denylist. No shell=True, direct binary execution.",
            SubAgentType.WEB_SEARCH: "Search the web for information using DuckDuckGo or Brave.",
            SubAgentType.WORKSPACE: "Inspect git status, search for files, search code contents, or list directory tree across the workspace repository.",
        }

        available_desc = "\n".join(
            f"- {agent_descriptions.get(t, 'Unknown agent')}" for t in allowed_types
        )

        system_prompt = f"""You are a task planner for an AI agent system. Decompose the user's request into a sequence of steps that can be executed by the available sub-agents.

Available sub-agents:
{available_desc}

Rules:
1. Output ONLY valid JSON - a list of step objects.
2. Each step must have: "agent_type", "payload", "priority" (int), "goal_criterion" (string to verify success).
3. Use only agent types from the available list above.
4. For code_analyzer: payload needs "files" (list of paths), "focus" (security/general/performance), "context".
5. For file_ops: payload needs "operation" (read/write/list/delete/mkdir), "path", optional "content" for write.
6. For shell_execution: payload needs "command", optional "timeout".
7. Set goal_criterion to a SPECIFIC, VERIFIABLE string that MUST appear in the step's output to confirm success.
   - For code_analyzer: use keywords like "security", "vulnerability", "bug", "performance", "style", "recommendation"
   - For file_ops: use the filename or "file created", "file deleted", "directory listed"
   - For shell_execution: use expected output text like command name, success message, or output pattern
7. If no files are referenced in the query, code_analyzer and file_ops should receive empty file lists.

Example output:
[
  {{"agent_type": "code_analyzer", "payload": {{"files": ["src/main.py"], "focus": "security", "context": "User wants security audit"}}, "priority": 10, "goal_criterion": "security"}},
  {{"agent_type": "file_ops", "payload": {{"operation": "read", "path": "README.md"}}, "priority": 5, "goal_criterion": "README"}},
  {{"agent_type": "shell_execution", "payload": {{"command": "python test.py"}}, "priority": 5, "goal_criterion": "test passed"}}
]"""

        user_prompt = f"User request: {query}\n\nContext: {context}\n\nDecompose this into steps."

        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_prompt),
        ]

        try:
            stream = client.chat_stream(messages, model=model, models=models)
            full_response = ""
            async for chunk in stream:
                full_response += chunk

            # Parse JSON from response
            plan = robust_json_loads(full_response.strip())
            if not isinstance(plan, list):
                raise TypeError("Planner response is not a list")

            # Validate and filter against allowed_types
            validated_tasks: list[dict[str, Any]] = []
            for step in plan:
                if not isinstance(step, dict):
                    continue
                agent_type_str = step.get("agent_type", "")
                try:
                    agent_type_enum = SubAgentType(agent_type_str)
                except ValueError:
                    continue

                if agent_type_enum in allowed_types:
                    # Ensure required fields
                    step.setdefault("payload", {})
                    step.setdefault("priority", 1)
                    step.setdefault("goal_criterion", "")
                    validated_tasks.append(step)
                elif (
                    SubAgentType.CODE_ANALYZER in allowed_types
                    and agent_type_enum != SubAgentType.CODE_ANALYZER
                ):
                    # Fallback to code analyzer
                    validated_tasks.append(
                        {
                            "agent_type": SubAgentType.CODE_ANALYZER.value,
                            "payload": {
                                "files": self._extract_file_paths(query),
                                "focus": "general",
                                "context": f"Fallback from {agent_type_str} for: {query}",
                            },
                            "priority": 1,
                            "goal_criterion": "",
                        }
                    )

            if not validated_tasks:
                # Fallback to heuristic if LLM produces no valid tasks
                return self._generate_plan(query, context, available_agents)

            return validated_tasks

        except (json.JSONDecodeError, OpenRouterError, ValueError, TypeError) as exc:
            # Fallback to heuristic on any error
            self._logger.warning("LLM planning failed, falling back to heuristic: %s", exc)
            return self._generate_plan(query, context, available_agents)

    def _extract_file_paths(self, text: str) -> list[str]:
        """Extract file paths from text using simple heuristics."""
        patterns = [
            r"@([\w./\\\-]+\.\w+)",  # @file.py (supports Windows backslash)
            r"`([^`]+\.\w+)`",  # `file.py`
            r"\"([^\"]+\.\w+)\"",  # "file.py"
            r"'([^']+\.\w+)'",  # 'file.py'
            r"\b([\w/\\.-]+\.(?:py|js|ts|tsx|java|go|rs|c|cpp|h|rb|php|sh|toml|yaml|yml|json|md|txt))\b",
        ]

        files: list[str] = []
        for pattern in patterns:
            matches = re.findall(pattern, text)
            files.extend(matches)

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_files: list[str] = []
        for f in files:
            if f not in seen:
                seen.add(f)
                unique_files.append(f)

        return unique_files

    def _infer_file_operation(self, text: str) -> str:
        """Infer the file operation from text."""
        text_lower = text.lower()
        if any(w in text_lower for w in ["read", "view", "show", "cat"]):
            return "read"
        elif any(w in text_lower for w in ["write", "create", "save"]):
            return "write"
        elif any(w in text_lower for w in ["delete", "remove", "rm"]):
            return "delete"
        elif any(w in text_lower for w in ["list", "ls", "dir"]):
            return "list"
        return "read"

    def _extract_command(self, text: str) -> str:
        """Extract shell command from text."""
        patterns = [
            r"```(?:bash|sh|shell)?\n(.*?)\n```",
            r"run\s+[\"']([^\"']+)[\"']",
            r"execute\s+[\"']([^\"']+)[\"']",
            r"command\s+[\"']([^\"']+)[\"']",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()

        return ""
