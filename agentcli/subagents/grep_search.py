"""Grep Search sub-agent (Phase 31).

High-speed regex and exact text pattern search across workspaces.
Accelerated by ripgrep (`rg`) when available on PATH, with an in-process
zero-dependency streaming Python regex fallback.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

if TYPE_CHECKING:
    from .bus import MessageBus

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 50
MAX_FILE_SIZE_BYTES = 1_000_000  # 1MB
DEFAULT_IGNORES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        ".pytest-temp",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)


class GrepSearchAgent(SubAgent):
    """Sub-agent for high-speed workspace-wide regex and literal text searching."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        message_bus: MessageBus | None = None,
    ) -> None:
        super().__init__(SubAgentType.GREP_SEARCH, config, message_bus)
        self.working_dir = str(self.config.get("working_dir") or Path.cwd())
        self.rg_path = shutil.which("rg")

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        payload = task.payload
        query = str(payload.get("query", "")).strip()

        if not query:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error="No search query provided for grep_search",
            )

        raw_path = str(payload.get("path") or payload.get("search_path") or self.working_dir)
        target_path = (
            Path(raw_path).resolve()
            if Path(raw_path).is_absolute()
            else (Path(self.working_dir) / raw_path).resolve()
        )

        if not target_path.exists():
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=f"Search path does not exist: {target_path}",
            )

        is_regex = bool(payload.get("is_regex", False))
        case_sensitive = bool(payload.get("case_sensitive", False))
        match_per_line = bool(payload.get("match_per_line", True))
        max_results = max(1, int(payload.get("max_results", DEFAULT_MAX_RESULTS)))
        includes: list[str] = list(payload.get("includes", []))
        excludes: list[str] = list(payload.get("excludes", []))

        # Try native ripgrep if available
        if self.rg_path:
            rg_result = await self._run_ripgrep(
                task_id=task.id,
                target_path=target_path,
                query=query,
                is_regex=is_regex,
                case_sensitive=case_sensitive,
                match_per_line=match_per_line,
                max_results=max_results,
                includes=includes,
                excludes=excludes,
            )
            if rg_result is not None:
                return rg_result

        # Fallback to Python in-process scanner
        return self._run_python_grep(
            task_id=task.id,
            target_path=target_path,
            query=query,
            is_regex=is_regex,
            case_sensitive=case_sensitive,
            match_per_line=match_per_line,
            max_results=max_results,
            includes=includes,
            excludes=excludes,
        )

    async def _run_ripgrep(
        self,
        task_id: str,
        target_path: Path,
        query: str,
        is_regex: bool,
        case_sensitive: bool,
        match_per_line: bool,
        max_results: int,
        includes: list[str],
        excludes: list[str],
    ) -> SubAgentResult | None:
        """Execute search using native ripgrep binary."""
        import asyncio

        cmd = [self.rg_path or "rg", "--json"]

        if not is_regex:
            cmd.append("--fixed-strings")
        if case_sensitive:
            cmd.append("--case-sensitive")
        else:
            cmd.append("--ignore-case")

        for inc in includes:
            cmd.extend(["--glob", inc])
        for exc in excludes:
            cmd.extend(["--glob", f"!{exc}"])

        # Cap results inside rg
        cmd.extend(["--max-count", str(max_results)])
        cmd.extend(["--", query, str(target_path)])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode not in (0, 1):  # 1 means no matches found in rg
                logger.debug("ripgrep exited with code %d: %s", proc.returncode, stderr.decode())
                return None

            matches: list[dict[str, Any]] = []
            files_set: set[str] = set()

            for line in stdout.decode("utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if data.get("type") == "match":
                        match_data = data.get("data", {})
                        path_text = match_data.get("path", {}).get("text", "")
                        try:
                            rel_file = str(Path(path_text).relative_to(self.working_dir)).replace("\\", "/")
                        except ValueError:
                            rel_file = path_text.replace("\\", "/")

                        files_set.add(rel_file)
                        line_num = match_data.get("line_number", 0)
                        line_text = match_data.get("lines", {}).get("text", "").rstrip("\r\n")

                        matches.append(
                            {
                                "file": rel_file,
                                "line_number": line_num,
                                "line_content": line_text,
                            }
                        )
                        if len(matches) >= max_results:
                            break
                except (json.JSONDecodeError, KeyError):
                    continue

            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=True,
                output={
                    "engine": "ripgrep",
                    "query": query,
                    "total_matches": len(matches) if match_per_line else len(files_set),
                    "matches": matches if match_per_line else sorted(files_set),
                    "files": sorted(files_set),
                    "truncated": len(matches) >= max_results,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("ripgrep failed, falling back to Python grep: %s", exc)
            return None

    def _run_python_grep(
        self,
        task_id: str,
        target_path: Path,
        query: str,
        is_regex: bool,
        case_sensitive: bool,
        match_per_line: bool,
        max_results: int,
        includes: list[str],
        excludes: list[str],
    ) -> SubAgentResult:
        """In-process fallback grep scanner."""
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query if is_regex else re.escape(query), flags)
        except re.error as exc:
            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=False,
                error=f"Invalid regex pattern '{query}': {exc}",
            )

        matches: list[dict[str, Any]] = []
        files_set: set[str] = set()

        def should_include_file(file_name: str, rel_path: str) -> bool:
            if includes and not any(
                fnmatch.fnmatch(file_name, p) or fnmatch.fnmatch(rel_path, p) for p in includes
            ):
                return False
            return not (
                excludes
                and any(
                    fnmatch.fnmatch(file_name, p) or fnmatch.fnmatch(rel_path, p) for p in excludes
                )
            )

        if target_path.is_file():
            file_candidates = [target_path]
        else:
            file_candidates = []
            for root, dirs, files in os.walk(target_path):
                dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORES and not d.endswith(".egg-info")]
                for f in files:
                    file_candidates.append(Path(root, f))

        for file_path in file_candidates:
            try:
                rel_path = str(file_path.relative_to(self.working_dir)).replace("\\", "/")
            except ValueError:
                rel_path = str(file_path).replace("\\", "/")

            if not should_include_file(file_path.name, rel_path):
                continue

            try:
                if file_path.stat().st_size > MAX_FILE_SIZE_BYTES:
                    continue
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            lines = content.splitlines()
            for line_idx, line in enumerate(lines, start=1):
                if pattern.search(line):
                    files_set.add(rel_path)
                    if match_per_line:
                        matches.append(
                            {
                                "file": rel_path,
                                "line_number": line_idx,
                                "line_content": line,
                            }
                        )
                    if (len(matches) if match_per_line else len(files_set)) >= max_results:
                        break
            if (len(matches) if match_per_line else len(files_set)) >= max_results:
                break

        return SubAgentResult(
            task_id=task_id,
            agent_type=self.agent_type,
            success=True,
            output={
                "engine": "python",
                "query": query,
                "total_matches": len(matches) if match_per_line else len(files_set),
                "matches": matches if match_per_line else sorted(files_set),
                "files": sorted(files_set),
                "truncated": (len(matches) if match_per_line else len(files_set)) >= max_results,
            },
        )
