"""Worktree subagent for Git sandboxing in Phase 34."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..worktree.manager import WorktreeManager
from .base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

logger = logging.getLogger(__name__)


class WorktreeAgent(SubAgent):
    """Subagent handling Git worktree sandboxing, diffing, and merging."""

    def __init__(
        self,
        manager: WorktreeManager | None = None,
        workspace_dir: str | Path | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(agent_type=SubAgentType.WORKTREE)
        self.manager = manager or WorktreeManager(repo_root=workspace_dir)
        self._config = config or {}

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Execute worktree management actions."""
        payload = task.payload or {}
        action = payload.get("action", "list").lower()

        try:
            if action == "list":
                worktrees = [w.to_dict() for w in self.manager.list_worktrees()]
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=task.agent_type,
                    success=True,
                    output={"worktrees": worktrees, "total": len(worktrees)},
                )

            if action in ("create", "add", "new"):
                branch_name = str(payload.get("branch", payload.get("name", ""))).strip()
                base_ref = payload.get("base_ref")
                if not branch_name:
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=task.agent_type,
                        success=False,
                        error="Missing 'branch' name for worktree creation.",
                        output={"error": "Missing 'branch' parameter."},
                    )

                meta = self.manager.create_worktree(branch_name=branch_name, base_ref=base_ref)
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=task.agent_type,
                    success=True,
                    output={
                        "created": True,
                        "worktree": meta.to_dict(),
                        "message": f"Created worktree for branch '{meta.branch}' at {meta.path}",
                    },
                )

            if action in ("status", "info"):
                branch = str(payload.get("branch", payload.get("name", ""))).strip()
                if not branch:
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=task.agent_type,
                        success=False,
                        error="Missing 'branch' parameter for worktree status.",
                        output={"error": "Missing 'branch' parameter."},
                    )
                status_data = self.manager.get_worktree_status(branch)
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=task.agent_type,
                    success=True,
                    output=status_data,
                )

            if action == "diff":
                branch = str(payload.get("branch", payload.get("name", ""))).strip()
                base_ref = payload.get("base_ref")
                if not branch:
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=task.agent_type,
                        success=False,
                        error="Missing 'branch' parameter for worktree diff.",
                        output={"error": "Missing 'branch' parameter."},
                    )
                diff_text = self.manager.compute_diff(branch, base_ref=base_ref)
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=task.agent_type,
                    success=True,
                    output={"branch": branch, "diff": diff_text},
                )

            if action == "merge":
                branch = str(payload.get("branch", payload.get("name", ""))).strip()
                target_branch = payload.get("target_branch")
                strategy = str(payload.get("strategy", "squash"))
                commit_message = payload.get("commit_message")

                if not branch:
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=task.agent_type,
                        success=False,
                        error="Missing 'branch' parameter for worktree merge.",
                        output={"error": "Missing 'branch' parameter."},
                    )

                merge_res = self.manager.merge_worktree(
                    branch_or_id=branch,
                    target_branch=target_branch,
                    strategy=strategy,
                    commit_message=commit_message,
                )
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=task.agent_type,
                    success=bool(merge_res.get("success")),
                    output=merge_res,
                    error=merge_res.get("error"),
                )

            if action in ("remove", "delete", "discard"):
                branch = str(payload.get("branch", payload.get("name", ""))).strip()
                force = bool(payload.get("force", True))
                delete_branch = bool(payload.get("delete_branch", False))

                if not branch:
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=task.agent_type,
                        success=False,
                        error="Missing 'branch' parameter for worktree removal.",
                        output={"error": "Missing 'branch' parameter."},
                    )

                removed = self.manager.remove_worktree(
                    branch_or_id=branch, force=force, delete_branch=delete_branch
                )
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=task.agent_type,
                    success=removed,
                    output={"removed": removed, "branch": branch},
                    error=None if removed else f"Failed to remove worktree '{branch}'.",
                )

            if action == "prune":
                pruned = self.manager.prune_all()
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=task.agent_type,
                    success=True,
                    output={"pruned_count": pruned},
                )

            return SubAgentResult(
                task_id=task.id,
                agent_type=task.agent_type,
                success=False,
                error=f"Unknown worktree action: '{action}'",
                output={"error": f"Unknown action: '{action}'"},
            )

        except Exception as exc:
            logger.exception("Error in WorktreeAgent for action '%s'", action)
            return SubAgentResult(
                task_id=task.id,
                agent_type=task.agent_type,
                success=False,
                error=str(exc),
                output={"error": str(exc)},
            )
