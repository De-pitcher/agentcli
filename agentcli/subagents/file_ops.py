"""File Operations sub-agent.

Handles file CRUD operations with path validation and safety checks.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..files import is_hidden_or_system
from ..policy import tool_policy
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
        self.working_dir = self.policy.root
        from ..agent.checkpoints import CheckpointManager

        self.journal: CheckpointManager = self.config.get("journal") or CheckpointManager(
            policy=tool_policy(self.config)
        )
        self.allow_outside = bool(self.config.get("allow_outside_working_dir", False))
        # Read-only by default unless allow_write or read_only=False is explicitly configured
        self.read_only = bool(
            self.config.get("read_only", not self.config.get("allow_write", False))
        )

    def _resolve_path(self, path: str, operation: str = "") -> Path:
        """Resolve and validate a file path under the effective policy.

        Args:
            path: The path to resolve (can be relative or absolute).
            operation: Optional operation being performed (e.g. "read", "list", "write").

        Returns:
            Resolved Path object.

        Raises:
            ValueError: If path is outside working directory and not allowed.
        """
        is_mutation = operation in {"write", "create", "append", "delete", "mkdir"}
        pol = tool_policy(self.config)
        return pol.path(path, write=is_mutation)

    def _atomic_write(self, path: Path, data: bytes) -> None:
        """Safely write data to a file atomically via tempfile replacement."""
        policy = tool_policy(self.config)
        if len(data) > min(policy.max_file_bytes, 1048576):
            raise ValueError("Mutation exceeds restorable file byte limit")
        fd, name = tempfile.mkstemp(prefix=".agentcli-write-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                try:
                    os.chmod(temporary, path.stat().st_mode)
                except OSError:
                    pass
            policy.path(path, write=True)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                try:
                    os.chmod(temporary, 0o600)
                except OSError:
                    pass
                try:
                    temporary.unlink()
                except OSError:
                    pass

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Parent dispatch for file operations.

        In worker mode: executes _run_local directly.
        In parent mode: captures pre-state, dispatches to helper, acquires post-state,
        and preserves cancellation cleanly.
        """
        from ..bounds import bound_result
        from ..io_boundary import filesystem_request, in_worker, query, unpack_snapshot
        from ..policy import PolicyDenied, active_policy

        if in_worker:
            return await self._run_local(task)

        policy = tool_policy(self.config)
        is_governed = active_policy.get() is not None

        payload = task.payload
        if not isinstance(payload, dict):
            res = SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error="Payload must be a dictionary",
            )
            return res if is_governed else bound_result(res, policy.max_output_bytes)

        op_raw = payload.get("operation")
        if not isinstance(op_raw, str) or not op_raw.strip():
            res = SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error="No operation specified",
            )
            return res if is_governed else bound_result(res, policy.max_output_bytes)
        operation = op_raw.strip().lower()

        path_raw = next(
            (
                payload.get(k)
                for k in ("path", "file_path", "filename", "file", "target")
                if payload.get(k) is not None
            ),
            None,
        )
        if path_raw is None or not isinstance(path_raw, str) or not path_raw.strip():
            res = SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error="No path specified",
            )
            return res if is_governed else bound_result(res, policy.max_output_bytes)
        path = path_raw

        checkpoint_id: str | None = None

        try:
            if operation in {"write", "create", "append", "delete"} and not self.read_only:
                if operation in {"write", "create", "append"}:
                    content = payload.get("content", "")
                    encoding = payload.get("encoding", "utf-8")
                    if not isinstance(content, str) or not isinstance(encoding, str):
                        raise ValueError("Content and encoding must be strings")
                    data = content.encode(encoding)
                    limit = min(policy.max_file_bytes, 1048576)
                    if len(data) > limit:
                        raise ValueError("Mutation exceeds restorable file byte limit")

                # Capture pre-state via helper
                snapshot = unpack_snapshot(
                    await query(filesystem_request("capture", policy, path=path), policy)
                )
                if operation == "append":
                    content = payload.get("content", "")
                    encoding = payload.get("encoding", "utf-8")
                    data = content.encode(encoding) if isinstance(content, str) else b""
                    limit = min(policy.max_file_bytes, 1048576)
                    if len(snapshot.data or b"") + len(data) > limit:
                        raise ValueError("Mutation exceeds restorable file byte limit")

                checkpoint_id = self.journal.create_checkpoint(f"{operation}: {snapshot.rel_path}")
                self.journal.record_snapshot(snapshot)

            # Primary mutation query with cancellation preservation
            primary_exc: BaseException | None = None
            try:
                req = filesystem_request(
                    "file_ops",
                    policy,
                    payload=payload,
                    task_id=task.id,
                    read_only=self.read_only,
                )
                h_res = await query(req, policy)
                result = SubAgentResult(
                    task_id=task.id,
                    agent_type=task.agent_type,
                    success=h_res.get("success", False),
                    output=h_res.get("output"),
                    error=h_res.get("error"),
                )
            except BaseException as exc:
                primary_exc = exc
                raise
            finally:
                if checkpoint_id:
                    cleanup_budget = min(5.0, policy.timeout_seconds)
                    post_policy = replace(policy, timeout_seconds=cleanup_budget)
                    checkpoint = self.journal._get_checkpoint(checkpoint_id)
                    if checkpoint is not None:
                        for snap_path, snapshot in list(checkpoint.files.items()):
                            try:
                                fp_req = filesystem_request(
                                    "fingerprint", post_policy, path=snap_path
                                )
                                snapshot.expected = await query(fp_req, post_policy)
                                snapshot.sealed = True
                                from ..agent.checkpoints import unchanged

                                if unchanged(snapshot):
                                    checkpoint.files.pop(snap_path)
                            except BaseException as cleanup_exc:
                                if primary_exc is not None and isinstance(
                                    primary_exc, asyncio.CancelledError
                                ):
                                    raise primary_exc from cleanup_exc
                                raise
                        self.journal.discard_empty_checkpoint(checkpoint_id)

            if not is_governed:
                return bound_result(result, policy.max_output_bytes)
            return result

        except (
            PolicyDenied,
            ValueError,
            TypeError,
            OSError,
            TimeoutError,
            NotImplementedError,
        ) as exc:
            if is_governed:
                raise
            err_msg = str(exc)
            if isinstance(exc, PolicyDenied):
                if "outside the granted workspace" in err_msg:
                    err_msg = f"Operation failed: Path is outside working directory ({exc})"
                elif "read-only mode" in err_msg:
                    err_msg = f"Operation '{operation}' is not permitted in read-only mode ({exc})"
            res = SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=err_msg,
            )
            return bound_result(res, policy.max_output_bytes)

    async def _run_local(self, task: SubAgentTask) -> SubAgentResult:
        """Execute a file operation task locally within active policy context.

        Expected payload:
            - operation: "read" | "write" | "delete" | "list" | "mkdir" | "exists" | "stat" | "append" | "create"
            - path: target file/directory path
            - content: content to write (for write/append/create)
            - encoding: text encoding (default: utf-8)
        """
        payload = task.payload or {}
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
            resolved_path = self._resolve_path(path, operation=operation)

            if self.read_only and operation in ("write", "delete", "mkdir", "create", "append"):
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=False,
                    error=f"Operation '{operation}' is not permitted in read-only mode. Use --allow-write to enable mutations.",
                )

            policy = tool_policy(self.config)

            if operation == "read":
                if not resolved_path.exists():
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=self.agent_type,
                        success=False,
                        error=f"File not found: {path}",
                    )
                encoding = payload.get("encoding", "utf-8")
                if not isinstance(encoding, str):
                    raise ValueError("Encoding must be a string")
                with resolved_path.open("rb") as stream:
                    data = stream.read(policy.max_file_bytes + 1)
                if len(data) > policy.max_file_bytes:
                    raise ValueError("File read exceeds byte limit")
                content = data.decode(encoding, errors="replace")
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=True,
                    output={"content": content, "path": str(resolved_path)},
                )

            elif operation in ("write", "create"):
                content = payload.get("content", "")
                encoding = payload.get("encoding", "utf-8")
                if not isinstance(content, str) or not isinstance(encoding, str):
                    raise ValueError("Content and encoding must be strings")
                prepared_data = content.encode(encoding)
                limit = min(policy.max_file_bytes, 1048576)
                if len(prepared_data) > limit:
                    raise ValueError("Mutation exceeds restorable file byte limit")
                resolved_path.parent.mkdir(parents=True, exist_ok=True)
                self._atomic_write(resolved_path, prepared_data)
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=True,
                    output={
                        "path": str(resolved_path),
                        "bytes_written": len(prepared_data),
                    },
                )

            elif operation == "append":
                content = payload.get("content", "")
                encoding = payload.get("encoding", "utf-8")
                if not isinstance(content, str) or not isinstance(encoding, str):
                    raise ValueError("Content and encoding must be strings")
                prepared_data = content.encode(encoding)
                appended_bytes = len(prepared_data)
                limit = min(policy.max_file_bytes, 1048576)
                if resolved_path.exists():
                    with resolved_path.open("rb") as stream:
                        original = stream.read(limit + 1)
                    prepared_data = original + prepared_data
                if len(prepared_data) > limit:
                    raise ValueError("Mutation exceeds restorable file byte limit")
                resolved_path.parent.mkdir(parents=True, exist_ok=True)
                self._atomic_write(resolved_path, prepared_data)
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=True,
                    output={
                        "path": str(resolved_path),
                        "bytes_appended": appended_bytes,
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
                    raise ValueError(
                        "Directory deletion is unsupported; file undo cannot restore directories"
                    )
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
                from ..bounds import walk_entries

                include_hidden = bool(payload.get("include_hidden", payload.get("all", False)))
                items: list[dict[str, Any]] = []
                # The shared walker counts native acquisitions before filtering,
                # rejects protected/link entries before metadata access and closes
                # scandir on exhaustion/error. An exhausted budget fails closed.
                for item, _, directory in walk_entries(
                    resolved_path, policy, max_entries=4096, max_depth=1
                ):
                    if not include_hidden and is_hidden_or_system(item):
                        continue
                    try:
                        size = item.stat().st_size if item.is_file() else None
                    except OSError:
                        size = None
                    items.append(
                        {
                            "name": item.name,
                            "path": str(item),
                            "is_dir": directory,
                            "size": size,
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
                size = None
                if is_file:
                    try:
                        size = resolved_path.stat().st_size
                    except OSError:
                        size = None
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
