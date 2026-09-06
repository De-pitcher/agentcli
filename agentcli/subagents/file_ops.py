"""File Operations sub-agent.

Handles file CRUD operations with path validation and safety checks.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

if TYPE_CHECKING:
    from .bus import MessageBus


class FileOpsAgent(SubAgent):
    """Sub-agent for file system operations.

    Provides safe file CRUD operations with path validation
    to prevent directory traversal and unauthorized access.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        message_bus: MessageBus | None = None,
    ) -> None:
        super().__init__(SubAgentType.FILE_OPS, config, message_bus)
        self.working_dir = Path(self.config.get("working_dir", os.getcwd())).resolve()
        self.allow_outside = bool(self.config.get("allow_outside_working_dir", False))
        # Phase 10: Read-only by default unless allow_write or read_only=False is explicitly configured
        self.read_only = bool(
            self.config.get("read_only", not self.config.get("allow_write", False))
        )

    def _resolve_path(self, path: str) -> Path:
        """Resolve and validate a file path.

        Args:
            path: The path to resolve (can be relative or absolute).

        Returns:
            Resolved Path object.

        Raises:
            ValueError: If path is outside working directory and not allowed.
        """
        path_obj = Path(path)
        if not path_obj.is_absolute():
            path_obj = (self.working_dir / path_obj).resolve()
        else:
            path_obj = path_obj.resolve()

        # Check if path is within working directory
        if not self.allow_outside:
            try:
                path_obj.relative_to(self.working_dir)
            except ValueError:
                raise ValueError(
                    f"Path '{path}' is outside working directory '{self.working_dir}' "
                    "and cross-directory access is not enabled"
                )

        return path_obj

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Execute a file operation task.

        Expected payload:
            - operation: "read" | "write" | "delete" | "list" | "mkdir"
            - path: target file/directory path
            - content: content to write (for write operation)
            - encoding: text encoding (default: utf-8)
        """
        payload = task.payload
        operation = payload.get("operation", "").lower()
        path = (
            payload.get("path")
            or payload.get("file_path")
            or payload.get("filename")
            or payload.get("file")
            or payload.get("target")
            or ""
        )

        if not operation:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error="No operation specified",
            )

        if not path:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error="No path specified",
            )

        try:
            resolved_path = self._resolve_path(path)

            if self.read_only and operation in ("write", "delete", "mkdir", "create", "append"):
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=False,
                    error=f"Operation '{operation}' is not permitted in read-only mode. Use --allow-write to enable mutations.",
                )

            if operation == "read":
                if not resolved_path.exists():
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=self.agent_type,
                        success=False,
                        error=f"File not found: {path}",
                    )
                encoding = payload.get("encoding", "utf-8")
                content = resolved_path.read_text(encoding=encoding)
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=True,
                    output={"content": content, "path": str(resolved_path)},
                )

            elif operation in ("write", "create"):
                content = payload.get("content", "")
                encoding = payload.get("encoding", "utf-8")
                resolved_path.parent.mkdir(parents=True, exist_ok=True)
                resolved_path.write_text(content, encoding=encoding, newline="")
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=True,
                    output={
                        "path": str(resolved_path),
                        "bytes_written": len(content.encode(encoding)),
                    },
                )

            elif operation == "append":
                content = payload.get("content", "")
                encoding = payload.get("encoding", "utf-8")
                resolved_path.parent.mkdir(parents=True, exist_ok=True)
                with resolved_path.open("a", encoding=encoding, newline="") as f:
                    f.write(content)
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=True,
                    output={
                        "path": str(resolved_path),
                        "bytes_appended": len(content.encode(encoding)),
                    },
                )

            elif operation == "delete":
                if not resolved_path.exists():
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=self.agent_type,
                        success=False,
                        error=f"Path not found: {path}",
                    )
                if resolved_path.is_dir():
                    shutil.rmtree(resolved_path)
                else:
                    resolved_path.unlink()
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=True,
                    output={"path": str(resolved_path), "deleted": True},
                )

            elif operation == "list":
                if not resolved_path.exists():
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=self.agent_type,
                        success=False,
                        error=f"Path not found: {path}",
                    )
                if not resolved_path.is_dir():
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=self.agent_type,
                        success=False,
                        error=f"Not a directory: {path}",
                    )
                items = []
                for item in resolved_path.iterdir():
                    items.append(
                        {
                            "name": item.name,
                            "path": str(item),
                            "is_dir": item.is_dir(),
                            "size": item.stat().st_size if item.is_file() else None,
                        }
                    )
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=True,
                    output={"path": str(resolved_path), "items": items},
                )

            elif operation == "mkdir":
                resolved_path.mkdir(parents=True, exist_ok=True)
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=True,
                    output={"path": str(resolved_path), "created": True},
                )

            elif operation in ("exists", "stat"):
                exists = resolved_path.exists()
                is_file = resolved_path.is_file() if exists else False
                is_dir = resolved_path.is_dir() if exists else False
                size = resolved_path.stat().st_size if is_file else None
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=True,
                    output={
                        "path": str(resolved_path),
                        "exists": exists,
                        "is_file": is_file,
                        "is_dir": is_dir,
                        "size": size,
                    },
                )

            else:
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=False,
                    error=f"Unknown operation: {operation}",
                )

        except ValueError as e:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=str(e),
            )
        except OSError as e:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=f"Operation failed: {e}",
            )
