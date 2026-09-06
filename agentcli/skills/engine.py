"""Skill execution engine and recipe orchestrator for Phase 33."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .loader import SkillLoader
from .manifest import SkillManifest

logger = logging.getLogger(__name__)


@dataclass
class SkillExecutionResult:
    """Result emitted after executing a skill."""

    success: bool
    skill_name: str
    rendered_prompt: str
    output: Any = None
    error: str | None = None
    execution_mode: str = "loop"
    tools_used: list[str] | None = None


class SkillEngine:
    """Coordinates skill discovery, validation, context injection, and execution."""

    def __init__(
        self,
        loader: SkillLoader | None = None,
        workspace_dir: str | Path | None = None,
    ) -> None:
        self.workspace_dir = Path(workspace_dir).resolve() if workspace_dir else Path.cwd()
        self.loader = loader or SkillLoader(workspace_dir=self.workspace_dir)

    def _get_git_branch(self) -> str:
        """Get the current active git branch name, if any."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(self.workspace_dir),
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                return res.stdout.strip()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not determine git branch for workspace %s: %s", self.workspace_dir, exc)
        return "main"

    def prepare_skill(
        self,
        skill_name: str,
        arguments: dict[str, Any] | None = None,
        extra_context: dict[str, Any] | None = None,
    ) -> tuple[SkillManifest, str]:
        """Validate arguments and render prompt for a skill."""
        manifest = self.loader.get_skill(skill_name)
        if not manifest:
            raise KeyError(f"Skill '{skill_name}' not found. Run '/skill list' to see available skills.")

        args = arguments or {}
        context: dict[str, Any] = {
            "workspace_dir": str(self.workspace_dir),
            "workspace_name": self.workspace_dir.name,
            "active_branch": self._get_git_branch(),
        }
        if extra_context:
            context.update(extra_context)

        rendered_prompt = manifest.render_prompt(args, context=context)
        return manifest, rendered_prompt

    def list_available_skills(self) -> list[dict[str, Any]]:
        """Return a serializable list of all loaded skill summaries."""
        return [skill.to_dict() for skill in self.loader.list_skills()]
