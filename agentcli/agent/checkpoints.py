"""Filesystem Checkpoints & Atomic Rollback Manager (Phase 31).

Enables automatic turn snapshotting, diff inspection, and safe single-command
rollback (/undo) for agent actions and subagent executions.
"""

from __future__ import annotations

import difflib
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_CHECKPOINTS = 20
MAX_SNAPSHOT_FILE_BYTES = 2_000_000  # 2MB


@dataclass
class FileSnapshot:
    """Snapshot of a single file before mutation."""

    rel_path: str
    exists: bool
    content: str | None = None
    is_binary: bool = False


@dataclass
class Checkpoint:
    """Snapshot of workspace state at a specific point in time."""

    id: str
    description: str
    timestamp: float = field(default_factory=time.time)
    files: dict[str, FileSnapshot] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class CheckpointManager:
    """Manages workspace snapshots and rollback operations."""

    def __init__(self, root_dir: Path | str = ".") -> None:
        self.root_dir = Path(root_dir).resolve()
        self._checkpoints: list[Checkpoint] = []

    def record_file_before_write(self, rel_path: str, checkpoint: Checkpoint | None = None) -> None:
        """Record the state of a file before modifying or deleting it."""
        norm_path = Path(rel_path).as_posix()
        target_cp = checkpoint or (self._checkpoints[-1] if self._checkpoints else None)
        if target_cp is None:
            return

        if norm_path in target_cp.files:
            return  # Already recorded in this checkpoint

        full_path = (self.root_dir / norm_path).resolve()
        if not full_path.exists():
            target_cp.files[norm_path] = FileSnapshot(rel_path=norm_path, exists=False)
            return

        try:
            if full_path.stat().st_size > MAX_SNAPSHOT_FILE_BYTES:
                target_cp.files[norm_path] = FileSnapshot(rel_path=norm_path, exists=True, is_binary=True)
                return

            text = full_path.read_text(encoding="utf-8", errors="replace")
            target_cp.files[norm_path] = FileSnapshot(rel_path=norm_path, exists=True, content=text)
        except OSError:
            target_cp.files[norm_path] = FileSnapshot(rel_path=norm_path, exists=True, is_binary=True)

    def create_checkpoint(self, description: str = "Turn checkpoint", metadata: dict[str, Any] | None = None) -> str:
        """Create a new checkpoint tracking session mutations."""
        cp_id = uuid.uuid4().hex[:8]
        cp = Checkpoint(
            id=cp_id,
            description=description,
            metadata=metadata or {},
        )
        self._checkpoints.append(cp)

        # Evict oldest if exceeding limit
        if len(self._checkpoints) > MAX_CHECKPOINTS:
            self._checkpoints.pop(0)

        return cp_id

    def list_checkpoints(self) -> list[dict[str, Any]]:
        """List all available checkpoints."""
        return [
            {
                "id": cp.id,
                "description": cp.description,
                "timestamp": cp.timestamp,
                "file_count": len(cp.files),
                "files": list(cp.files.keys()),
            }
            for cp in reversed(self._checkpoints)
        ]

    def get_diff(self, checkpoint_id: str | None = None) -> str:
        """Generate unified diff showing changes made since the given checkpoint."""
        cp = self._get_checkpoint(checkpoint_id)
        if cp is None or not cp.files:
            return "No file changes recorded in this checkpoint."

        diffs: list[str] = []
        for rel_path, snap in cp.files.items():
            full_path = (self.root_dir / rel_path).resolve()
            current_content = ""
            if full_path.exists():
                try:
                    current_content = full_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    current_content = "[Binary or unreadable file]"

            orig_content = snap.content if (snap.exists and snap.content is not None) else ""
            orig_lines = orig_content.splitlines(keepends=True)
            curr_lines = current_content.splitlines(keepends=True)

            file_diff = "".join(
                difflib.unified_diff(
                    orig_lines,
                    curr_lines,
                    fromfile=f"a/{rel_path} (checkpoint {cp.id})",
                    tofile=f"b/{rel_path} (current)",
                )
            )
            if file_diff:
                diffs.append(file_diff)

        return "\n".join(diffs) if diffs else "No modified files found."

    def rollback(self, checkpoint_id: str | None = None) -> dict[str, Any]:
        """Revert all file changes recorded in the specified (or latest) checkpoint."""
        cp = self._get_checkpoint(checkpoint_id)
        if cp is None:
            return {"success": False, "error": "No checkpoint available to restore", "reverted_files": []}

        reverted: list[str] = []
        errors: list[str] = []

        for rel_path, snap in cp.files.items():
            full_path = (self.root_dir / rel_path).resolve()
            try:
                if not snap.exists:
                    # File did not exist originally: delete it if created
                    if full_path.exists():
                        full_path.unlink()
                        reverted.append(f"deleted {rel_path}")
                else:
                    # File existed: restore content
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    if snap.content is not None:
                        full_path.write_text(snap.content, encoding="utf-8")
                        reverted.append(f"restored {rel_path}")
            except OSError as exc:
                errors.append(f"Failed to revert {rel_path}: {exc}")

        # Remove the restored checkpoint
        if cp in self._checkpoints:
            self._checkpoints.remove(cp)

        return {
            "success": len(errors) == 0,
            "checkpoint_id": cp.id,
            "description": cp.description,
            "reverted_files": reverted,
            "errors": errors,
        }

    def _get_checkpoint(self, checkpoint_id: str | None) -> Checkpoint | None:
        if not self._checkpoints:
            return None
        if checkpoint_id is None:
            return self._checkpoints[-1]
        for cp in self._checkpoints:
            if cp.id == checkpoint_id:
                return cp
        return None
