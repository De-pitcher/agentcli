"""TaskManager subagent wrapping background task execution and lifecycle control."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

if TYPE_CHECKING:
    from ..agent.tasks import TaskManager

logger = logging.getLogger(__name__)


class TaskManagerAgent(SubAgent):
    """Sub-agent responsible for managing background commands and processes."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        task_manager: TaskManager | None = None,
    ) -> None:
        super().__init__(
            agent_type=SubAgentType.TASK_MANAGER,
            config=config or {},
        )
        if task_manager is not None:
            self.task_manager = task_manager
        else:
            from ..agent.tasks import TaskManager

            self.task_manager = TaskManager(root_dir=self.config.get("working_dir"))

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Execute task manager action."""
        payload = task.payload
        action = payload.get("action", "list").lower()

        try:
            if action in ("run", "start", "run_background"):
                command = payload.get("command")
                if not command:
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=self.agent_type,
                        success=False,
                        error="Missing required 'command' in payload",
                    )
                cwd = payload.get("cwd") or payload.get("working_dir")
                bg_task = await self.task_manager.start_task(command=command, cwd=cwd)
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=bg_task.status != "failed",
                    output=bg_task.to_detail(),
                    error=bg_task.output_lines[0] if bg_task.status == "failed" else None,
                )

            elif action == "list":
                tasks_list = self.task_manager.list_tasks()
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=True,
                    output={"tasks": tasks_list, "total": len(tasks_list)},
                )

            elif action == "status":
                task_id = payload.get("task_id")
                if not task_id:
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=self.agent_type,
                        success=False,
                        error="Missing required 'task_id' in payload",
                    )
                status_info = self.task_manager.get_status(task_id)
                success = "error" not in status_info
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=success,
                    output=status_info,
                    error=status_info.get("error"),
                )

            elif action in ("logs", "log"):
                task_id = payload.get("task_id")
                if not task_id:
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=self.agent_type,
                        success=False,
                        error="Missing required 'task_id' in payload",
                    )
                tail = int(payload.get("tail", 50))
                offset = int(payload.get("offset", 0))
                log_info = self.task_manager.get_logs(task_id, tail=tail, offset=offset)
                success = "error" not in log_info
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=success,
                    output=log_info,
                    error=log_info.get("error"),
                )

            elif action in ("send_input", "input"):
                task_id = payload.get("task_id")
                input_text = payload.get("input", "")
                if not task_id:
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=self.agent_type,
                        success=False,
                        error="Missing required 'task_id' in payload",
                    )
                res = await self.task_manager.send_input(task_id, input_text)
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=res.get("success", False),
                    output=res,
                    error=res.get("error"),
                )

            elif action in ("kill", "stop", "terminate"):
                task_id = payload.get("task_id")
                if not task_id:
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=self.agent_type,
                        success=False,
                        error="Missing required 'task_id' in payload",
                    )
                res = await self.task_manager.kill_task(task_id)
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=res.get("success", False),
                    output=res,
                    error=res.get("error"),
                )

            else:
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=False,
                    error=f"Unknown task manager action '{action}'. Supported: run, list, status, logs, send_input, kill",
                )

        except Exception as exc:
            logger.exception("TaskManagerAgent execution failed")
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=str(exc),
            )
