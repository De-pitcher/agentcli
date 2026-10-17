"""Byte-preserving, authorized file undo with explicit post-state conflicts."""

from __future__ import annotations

import difflib
import hashlib
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from ..policy import RunPolicy

MAX_SNAPSHOT_FILE_BYTES = 1048576
MAX_CHECKPOINTS = 20


def fingerprint(path: Path) -> str | None:
    from ..io_boundary import filesystem_request, in_worker, query_sync

    if not in_worker:
        return cast(
            str | None,
            query_sync(
                filesystem_request("fingerprint", RunPolicy(root=path.parent), path=str(path)),
                RunPolicy(root=path.parent),
            ),
        )
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > MAX_SNAPSHOT_FILE_BYTES:
        raise ValueError("Directories and oversized file undo are unsupported")
    info = path.stat()
    with path.open("rb") as stream:
        data = stream.read(MAX_SNAPSHOT_FILE_BYTES + 1)
    if len(data) > MAX_SNAPSHOT_FILE_BYTES:
        raise ValueError("File exceeds snapshot limit")
    return hashlib.sha256(data + str((info.st_mode, info.st_mtime_ns)).encode()).hexdigest()


@dataclass
class FileSnapshot:
    rel_path: str
    exists: bool
    content: str | None = None  # compatibility for display only; never restored
    is_binary: bool = False
    data: bytes | None = None
    mode: int | None = None
    mtime_ns: int | None = None
    atime_ns: int | None = None
    expected: str | None = None
    sealed: bool = False


def unchanged(snap: FileSnapshot) -> bool:
    if not snap.sealed:
        return False
    if not snap.exists:
        return snap.expected is None
    if snap.data is None:
        return False
    before = hashlib.sha256(snap.data + str((snap.mode, snap.mtime_ns)).encode()).hexdigest()
    return before == snap.expected


def capture(policy: RunPolicy, value: str) -> FileSnapshot:
    from ..io_boundary import filesystem_request, in_worker, query_sync, unpack_snapshot

    if not in_worker:
        return unpack_snapshot(
            query_sync(filesystem_request("capture", policy, path=value), policy)
        )
    path = policy.path(value, write=True)
    if not path.exists():
        return FileSnapshot(str(path), False)
    info = path.stat()
    if not path.is_file() or info.st_size > MAX_SNAPSHOT_FILE_BYTES:
        raise ValueError("Directories and oversized file undo are unsupported")
    with path.open("rb") as stream:
        data = stream.read(MAX_SNAPSHOT_FILE_BYTES + 1)
    if len(data) > MAX_SNAPSHOT_FILE_BYTES:
        raise ValueError("Snapshot exceeds byte limit")
    return FileSnapshot(
        str(path),
        True,
        data=data,
        mode=info.st_mode,
        mtime_ns=info.st_mtime_ns,
        atime_ns=info.st_atime_ns,
    )


def restore(policy: RunPolicy, snap: FileSnapshot) -> None:
    from ..io_boundary import filesystem_request, in_worker, pack_snapshot, query_sync

    if not in_worker:
        query_sync(filesystem_request("restore", policy, snapshot=pack_snapshot(snap)), policy)
        return
    path = policy.path(snap.rel_path, write=True)
    if not snap.sealed:
        raise ValueError("Undo unavailable: expected post-state was not recorded")
    if fingerprint(path) != snap.expected:
        raise ValueError("Undo conflict: file changed after the recorded operation")
    if not snap.exists:
        if path.exists():
            path.unlink()
        return
    if snap.data is None:
        raise ValueError("Undo unavailable: original bytes were not captured")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".agentcli-undo-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(snap.data)
            stream.flush()
            os.fsync(stream.fileno())
        policy.path(path, write=True)
        if fingerprint(path) != snap.expected:
            raise ValueError("Undo conflict during restoration")
        if snap.mode is not None:
            os.chmod(temporary, snap.mode)
        os.replace(temporary, path)
        if snap.mtime_ns is not None:
            os.utime(
                path, ns=(snap.mtime_ns if snap.atime_ns is None else snap.atime_ns, snap.mtime_ns)
            )
    finally:
        if temporary.exists():
            os.chmod(temporary, 0o600)
            temporary.unlink()


@dataclass
class Checkpoint:
    id: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)
    files: dict[str, FileSnapshot] = field(default_factory=dict)
    timestamp: str = ""


class CheckpointManager:
    def __init__(self, root_dir: Path | str = ".", policy: RunPolicy | None = None) -> None:
        self.policy = policy or RunPolicy(root=Path(root_dir))
        self.root_dir = self.policy.root
        self._checkpoints: list[Checkpoint] = []

    def create_checkpoint(
        self, description: str = "Turn checkpoint", metadata: dict[str, Any] | None = None
    ) -> str:
        cp = Checkpoint(uuid.uuid4().hex, description, metadata or {})
        self._checkpoints.append(cp)
        self._checkpoints = self._checkpoints[-MAX_CHECKPOINTS:]
        return cp.id

    def _get_checkpoint(self, checkpoint_id: str | None = None) -> Checkpoint | None:
        if checkpoint_id is None:
            return self._checkpoints[-1] if self._checkpoints else None
        return next((c for c in self._checkpoints if c.id == checkpoint_id), None)

    def record_file_before_write(self, rel_path: str, checkpoint: Checkpoint | None = None) -> None:
        cp = checkpoint or self._get_checkpoint()
        if cp is None:
            raise ValueError("No active checkpoint")
        path = str(self.policy.path(rel_path, write=True))
        if path not in cp.files:
            if len(cp.files) >= 128:
                raise ValueError("Checkpoint file count limit exceeded")
            snapshot = capture(self.policy, path)
            retained = sum(len(s.data or b"") for c in self._checkpoints for s in c.files.values())
            if retained + len(snapshot.data or b"") > 16 * MAX_SNAPSHOT_FILE_BYTES:
                raise ValueError("Journal retained byte limit exceeded")
            cp.files[path] = snapshot

    def record_snapshot(self, snapshot: FileSnapshot) -> None:
        """Retain a helper-validated preimage before dispatching a mutation."""
        cp = self._get_checkpoint()
        if cp is None:
            raise ValueError("No active checkpoint")
        if snapshot.rel_path in cp.files:
            return
        if len(cp.files) >= 128:
            raise ValueError("Checkpoint file count limit exceeded")
        retained = sum(len(s.data or b"") for c in self._checkpoints for s in c.files.values())
        if retained + len(snapshot.data or b"") > 16 * MAX_SNAPSHOT_FILE_BYTES:
            raise ValueError("Journal retained byte limit exceeded")
        cp.files[snapshot.rel_path] = snapshot

    def discard_empty_checkpoint(self, checkpoint_id: str) -> bool:
        cp = self._get_checkpoint(checkpoint_id)
        if cp is not None and not cp.files:
            self._checkpoints.remove(cp)
            return True
        return False

    def record_post_state(self, checkpoint_id: str | None = None) -> None:
        cp = self._get_checkpoint(checkpoint_id)
        if cp is None:
            raise ValueError("Checkpoint not found")
        for path, snap in list(cp.files.items()):
            snap.expected = fingerprint(self.policy.path(snap.rel_path, write=True))
            snap.sealed = True
            if unchanged(snap):
                cp.files.pop(path)
        self.discard_empty_checkpoint(cp.id)

    def list_checkpoints(self) -> list[dict[str, Any]]:
        return [
            {
                "id": c.id,
                "description": c.description,
                "timestamp": c.timestamp,
                "file_count": len(c.files),
                "files": list(c.files),
            }
            for c in reversed(self._checkpoints)
        ]

    def get_diff(self, checkpoint_id: str | None = None) -> str:
        cp = self._get_checkpoint(checkpoint_id)
        if cp is None:
            return "No file changes recorded in this checkpoint."
        from ..io_boundary import filesystem_request, in_worker, pack_snapshot, query_sync

        output: list[str] = []
        deadline = time.monotonic() + self.policy.timeout_seconds
        for path, snap in cp.files.items():
            if not in_worker:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Diff acquisition deadline exceeded")
                policy = replace(self.policy, timeout_seconds=remaining)
                diff = query_sync(
                    filesystem_request("display_diff", policy, snapshot=pack_snapshot(snap)), policy
                )
            else:
                from ..bounds import read_text

                resolved = self.policy.path(path)
                old = (snap.data or b"").decode("utf-8", errors="replace")
                new = read_text(resolved, self.policy) if resolved.exists() else ""
                diff = "".join(
                    difflib.unified_diff(
                        old.splitlines(True),
                        new.splitlines(True),
                        fromfile="a/" + path,
                        tofile="b/" + path,
                    )
                )
            if (
                sum(len(part.encode("utf-8")) for part in output) + len(diff.encode("utf-8"))
                > self.policy.max_output_bytes
            ):
                raise ValueError("Diff output exceeds limit; narrow checkpoint scope")
            output.append(diff)
        return "".join(output) or "No modified files found."

    def rollback(self, checkpoint_id: str | None = None) -> dict[str, Any]:
        cp = self._get_checkpoint(checkpoint_id)
        if cp is None:
            return {"success": False, "error": "No checkpoint available", "reverted_files": []}
        reverted, errors = [], []
        for snap in cp.files.values():
            if (
                not snap.sealed
                or fingerprint(self.policy.path(snap.rel_path, write=True)) != snap.expected
            ):
                return {
                    "success": False,
                    "error": "Undo conflict or missing expected post-state",
                    "reverted_files": [],
                }
        for path, snap in list(cp.files.items()):
            try:
                restore(self.policy, snap)
                reverted.append(
                    ("restored " if snap.exists else "deleted ")
                    + str(Path(path).relative_to(self.root_dir))
                )
                cp.files.pop(path)
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
        if not errors:
            self._checkpoints.remove(cp)
        return {
            "success": not errors,
            "error": "; ".join(errors) or None,
            "reverted_files": reverted,
        }
