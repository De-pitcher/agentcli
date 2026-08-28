"""Planner sub-agent.

Decomposes complex tasks into sub-tasks for dispatch to other agents.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

if TYPE_CHECKING:
    from .bus import MessageBus


class PlannerAgent(SubAgent):
    """Sub-agent for task decomposition and planning.

    Analyzes a user request and breaks it down into sub-tasks
    that can be executed by other specialized agents.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        message_bus: MessageBus | None = None,
    ) -> None:
        super().__init__(SubAgentType.PLANNER, config, message_bus)

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Decompose a task into sub-tasks.

        Expected payload:
            - query: user's request/question
            - context: optional additional context
            - available_agents: list of available agent types (optional)

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

        if not query:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error="No query provided for planning",
            )

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
            raw_tasks.append(
                {
                    "agent_type": SubAgentType.CODE_ANALYZER.value,
                    "payload": {
                        "files": self._extract_file_paths(query),
                        "focus": "security" if "security" in query_lower else "general",
                        "context": f"User requested: {query}",
                    },
                    "priority": 10,
                }
            )

        # File operations
        if any(
            keyword in query_lower
            for keyword in ["read", "write", "create", "delete", "list", "file"]
        ):
            raw_tasks.append(
                {
                    "agent_type": SubAgentType.FILE_OPS.value,
                    "payload": {
                        "operation": self._infer_file_operation(query),
                        "path": self._extract_file_paths(query)[0]
                        if self._extract_file_paths(query)
                        else "",
                    },
                    "priority": 5,
                }
            )

        # Shell execution
        if any(
            keyword in query_lower
            for keyword in ["run", "execute", "run command", "shell", "terminal"]
        ):
            cmd = self._extract_command(query)
            if cmd:
                raw_tasks.append(
                    {
                        "agent_type": SubAgentType.SHELL_EXECUTION.value,
                        "payload": {
                            "command": cmd,
                            "timeout": 30.0,
                        },
                        "priority": 5,
                    }
                )

        # Default to code analyzer if no specific agent matched
        if not raw_tasks:
            raw_tasks.append(
                {
                    "agent_type": SubAgentType.CODE_ANALYZER.value,
                    "payload": {
                        "files": self._extract_file_paths(query),
                        "focus": "general",
                        "context": f"User asked: {query}",
                    },
                    "priority": 1,
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
                    }
                )

        return validated_tasks

    def _extract_file_paths(self, text: str) -> list[str]:
        """Extract file paths from text using simple heuristics."""
        patterns = [
            r"@([\w./\-]+\.\w+)",  # @file.py
            r"`([^`]+\.\w+)`",  # `file.py`
            r"\"([^\"]+\.\w+)\"",  # "file.py"
            r"'([^']+\.\w+)'",  # 'file.py'
            r"\b([\w/.-]+\.(?:py|js|ts|tsx|java|go|rs|c|cpp|h|rb|php|sh|toml|yaml|yml|json|md|txt))\b",
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
