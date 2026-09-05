"""Workspace and repository context sub-agent.

Provides repository inspection, git grounding, filename search, code search,
and directory tree inspection without external dependencies.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

if TYPE_CHECKING:
    from .bus import MessageBus

# Common directories and files to ignore during repository traversal
DEFAULT_IGNORES: set[str] = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".pytest-temp",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    ".egg-info",
}


class WorkspaceAgent(SubAgent):
    """Sub-agent for workspace exploration, git grounding, and codebase search."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        message_bus: MessageBus | None = None,
    ) -> None:
        super().__init__(SubAgentType.WORKSPACE, config, message_bus)

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        payload = task.payload
        operation = payload.get("operation", "git_status")
        root_dir = Path(payload.get("path") or ".").resolve()

        if operation == "git_status":
            return await self._git_status(task.id, root_dir)
        elif operation == "search_files":
            pattern = payload.get("pattern", "*")
            return self._search_files(task.id, root_dir, pattern, payload.get("max_results", 50))
        elif operation == "search_code":
            query = payload.get("query", "")
            if not query:
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=False,
                    error="No search query provided for search_code",
                )
            is_regex = bool(payload.get("is_regex", False))
            case_sensitive = bool(payload.get("case_sensitive", False))
            return self._search_code(
                task.id,
                root_dir,
                query,
                is_regex=is_regex,
                case_sensitive=case_sensitive,
                max_results=payload.get("max_results", 30),
            )
        elif operation == "list_tree":
            max_depth = int(payload.get("max_depth", 2))
            return self._list_tree(task.id, root_dir, max_depth)
        elif operation == "git_branch":
            branch_name = payload.get("branch_name", "")
            action = payload.get("action", "create")
            return await self._git_branch(task.id, root_dir, branch_name, action=action)
        elif operation == "git_worktree":
            worktree_path = payload.get("worktree_path", "")
            branch_name = payload.get("branch_name")
            action = payload.get("action", "create")
            return await self._git_worktree(
                task.id, root_dir, worktree_path, branch_name=branch_name, action=action
            )
        else:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=f"Unsupported workspace operation '{operation}'",
            )

    async def _git_status(self, task_id: str, root_dir: Path) -> SubAgentResult:
        """Query git branch and status information."""
        try:
            branch_proc = await asyncio.create_subprocess_exec(
                "git",
                "branch",
                "--show-current",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(root_dir),
            )
            branch_out, _ = await branch_proc.communicate()
            branch = branch_out.decode(errors="replace").strip() or "HEAD"

            status_proc = await asyncio.create_subprocess_exec(
                "git",
                "status",
                "--short",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(root_dir),
            )
            status_out, _ = await status_proc.communicate()
            status_lines = [
                line.strip()
                for line in status_out.decode(errors="replace").splitlines()
                if line.strip()
            ]

            modified = [line for line in status_lines if not line.startswith("??")]
            untracked = [line for line in status_lines if line.startswith("??")]

            summary = f"Branch: {branch} | Modified: {len(modified)} file(s) | Untracked: {len(untracked)}"

            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=True,
                output={
                    "is_git_repo": True,
                    "branch": branch,
                    "modified_files": modified,
                    "untracked_files": untracked,
                    "summary": summary,
                },
            )
        except (FileNotFoundError, OSError) as exc:
            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=True,
                output={
                    "is_git_repo": False,
                    "branch": "none",
                    "modified_files": [],
                    "untracked_files": [],
                    "summary": f"Not a git repository or git unavailable ({exc})",
                },
            )

    def _search_files(
        self,
        task_id: str,
        root_dir: Path,
        pattern: str,
        max_results: int,
    ) -> SubAgentResult:
        """Search filenames in workspace matching glob pattern."""
        matches: list[str] = []
        pattern_lower = pattern.lower()

        for dirpath, dirnames, filenames in os.walk(root_dir):
            # Prune ignored directories in-place
            dirnames[:] = [
                d
                for d in dirnames
                if d not in DEFAULT_IGNORES and not any(d.endswith(ign) for ign in [".egg-info"])
            ]

            for file in filenames:
                if fnmatch.fnmatch(file.lower(), pattern_lower) or pattern_lower in file.lower():
                    rel_path = str(Path(dirpath, file).relative_to(root_dir)).replace("\\", "/")
                    matches.append(rel_path)
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break

        return SubAgentResult(
            task_id=task_id,
            agent_type=self.agent_type,
            success=True,
            output={
                "pattern": pattern,
                "matches": matches,
                "total_found": len(matches),
            },
        )

    def _search_code(
        self,
        task_id: str,
        root_dir: Path,
        query: str,
        *,
        is_regex: bool,
        case_sensitive: bool,
        max_results: int,
    ) -> SubAgentResult:
        """Search file contents across the workspace for query matching."""
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query if is_regex else re.escape(query), flags)
        except re.error as exc:
            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=False,
                error=f"Invalid regex pattern: {exc}",
            )

        results: list[dict[str, Any]] = []

        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORES]

            for file in filenames:
                file_path = Path(dirpath, file)
                # Skip large or binary files
                try:
                    if file_path.stat().st_size > 500_000:
                        continue
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue

                for line_no, line in enumerate(content.splitlines(), start=1):
                    if pattern.search(line):
                        rel_path = str(file_path.relative_to(root_dir)).replace("\\", "/")
                        results.append(
                            {
                                "file": rel_path,
                                "line": line_no,
                                "content": line.strip()[:200],
                            }
                        )
                        if len(results) >= max_results:
                            break
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break

        return SubAgentResult(
            task_id=task_id,
            agent_type=self.agent_type,
            success=True,
            output={
                "query": query,
                "matches": results,
                "total_matches": len(results),
            },
        )

    def _list_tree(self, task_id: str, root_dir: Path, max_depth: int) -> SubAgentResult:
        """List repository structure up to max_depth."""
        tree: list[str] = []

        def walk(current: Path, depth: int) -> None:
            if depth > max_depth:
                return
            try:
                entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except OSError:
                return

            for entry in entries:
                if entry.name in DEFAULT_IGNORES or entry.name.endswith(".egg-info"):
                    continue
                indent = "  " * (depth - 1)
                prefix = "📁 " if entry.is_dir() else "📄 "
                tree.append(f"{indent}{prefix}{entry.name}")
                if entry.is_dir():
                    walk(entry, depth + 1)

        walk(root_dir, 1)

        return SubAgentResult(
            task_id=task_id,
            agent_type=self.agent_type,
            success=True,
            output={
                "root": str(root_dir),
                "tree": tree,
            },
        )

    async def _git_branch(
        self,
        task_id: str,
        root_dir: Path,
        branch_name: str | None = None,
        action: str = "create",
    ) -> SubAgentResult:
        if action != "list" and not branch_name:
            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=False,
                error=f"No branch_name provided for git_branch '{action}'",
            )
        try:
            if action == "list":
                cmd = ["git", "branch", "--list"]
            elif action == "create":
                cmd = ["git", "checkout", "-b", str(branch_name)]
            elif action == "delete":
                cmd = ["git", "branch", "-D", str(branch_name)]
            else:
                cmd = ["git", "checkout", str(branch_name)]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(root_dir),
            )
            out, err = await proc.communicate()
            if proc.returncode == 0:
                return SubAgentResult(
                    task_id=task_id,
                    agent_type=self.agent_type,
                    success=True,
                    output={
                        "action": action,
                        "branch": branch_name or "",
                        "message": out.decode(errors="replace").strip()
                        or f"Branch operation '{action}' succeeded.",
                    },
                )
            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=False,
                error=err.decode(errors="replace").strip() or "Git branch operation failed",
            )
        except Exception as exc:  # noqa: BLE001
            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=False,
                error=f"Git branch execution failed: {exc}",
            )

    async def _git_worktree(
        self,
        task_id: str,
        root_dir: Path,
        worktree_path: str,
        branch_name: str | None = None,
        action: str = "create",
    ) -> SubAgentResult:
        if not worktree_path:
            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=False,
                error="No worktree_path provided for git_worktree",
            )
        try:
            if action == "create":
                cmd = ["git", "worktree", "add"]
                if branch_name:
                    cmd.extend(["-b", branch_name])
                cmd.append(worktree_path)
            elif action == "remove":
                cmd = ["git", "worktree", "remove", "--force", worktree_path]
            else:
                cmd = ["git", "worktree", "list"]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(root_dir),
            )
            out, err = await proc.communicate()
            if proc.returncode == 0:
                return SubAgentResult(
                    task_id=task_id,
                    agent_type=self.agent_type,
                    success=True,
                    output={
                        "action": action,
                        "worktree_path": worktree_path,
                        "message": out.decode(errors="replace").strip()
                        or f"Worktree operation '{action}' succeeded.",
                    },
                )
            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=False,
                error=err.decode(errors="replace").strip() or "Git worktree operation failed",
            )
        except Exception as exc:  # noqa: BLE001
            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=False,
                error=f"Git worktree execution failed: {exc}",
            )

