"""Skill runner subagent for Phase 33."""

from __future__ import annotations

import logging

from ..skills.engine import SkillEngine
from ..skills.loader import SkillLoader
from .base import SubAgent, SubAgentResult, SubAgentTask

logger = logging.getLogger(__name__)


class SkillRunnerAgent(SubAgent):
    """Subagent handling skill discovery, inspection, and execution."""

    def __init__(
        self,
        engine: SkillEngine | None = None,
        loader: SkillLoader | None = None,
        workspace_dir: str | None = None,
    ) -> None:
        self.loader = loader or SkillLoader(workspace_dir=workspace_dir)
        self.engine = engine or SkillEngine(loader=self.loader, workspace_dir=workspace_dir)

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Execute skill runner actions: list, info, reload, run."""
        payload = task.payload or {}
        action = payload.get("action", "list").lower()

        try:
            if action == "list":
                skills = self.engine.list_available_skills()
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=task.agent_type,
                    success=True,
                    output={"skills": skills, "total": len(skills)},
                )

            if action == "info":
                name = str(payload.get("name", payload.get("skill_name", ""))).strip()
                manifest = self.loader.get_skill(name)
                if not manifest:
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=task.agent_type,
                        success=False,
                        error=f"Skill '{name}' not found",
                        output={"error": f"Skill '{name}' not found"},
                    )
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=task.agent_type,
                    success=True,
                    output={"skill": manifest.to_dict(), "prompt_template": manifest.prompt_template},
                )

            if action == "reload":
                self.loader.reload()
                skills = self.engine.list_available_skills()
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=task.agent_type,
                    success=True,
                    output={"reloaded": True, "total": len(skills)},
                )

            if action in ("run", "execute"):
                name = str(payload.get("name", payload.get("skill_name", ""))).strip()
                args = payload.get("args", payload.get("arguments", {}))
                context = payload.get("context", {})

                manifest, rendered_prompt = self.engine.prepare_skill(
                    skill_name=name,
                    arguments=args,
                    extra_context=context,
                )

                return SubAgentResult(
                    task_id=task.id,
                    agent_type=task.agent_type,
                    success=True,
                    output={
                        "skill_name": manifest.name,
                        "execution_mode": manifest.execution_mode,
                        "max_iterations": manifest.max_iterations,
                        "required_tools": manifest.required_tools,
                        "rendered_prompt": rendered_prompt,
                    },
                )

            return SubAgentResult(
                task_id=task.id,
                agent_type=task.agent_type,
                success=False,
                error=f"Unknown skill_runner action: '{action}'",
                output={"error": f"Unknown action: '{action}'"},
            )

        except Exception as exc:
            logger.exception("Error executing skill runner action '%s'", action)
            return SubAgentResult(
                task_id=task.id,
                agent_type=task.agent_type,
                success=False,
                error=str(exc),
                output={"error": str(exc)},
            )
