"""Agent Auto-Healing & File Rollback Manager (Phase 36).

Provides transactional filesystem snapshots, automatic rollbacks when loops
or critical drift occur, and recovery prompt synthesis for resilient self-healing.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .checkpoints import MAX_SNAPSHOT_FILE_BYTES, FileSnapshot
from .drift_detector import DriftReport

logger = logging.getLogger(__name__)

MAX_HEALING_SNAPSHOTS = 20


@dataclass
class HealingSnapshot:
    """Snapshot of files captured prior to a risky or mutative agent action."""

    id: str
    iteration: int
    description: str
    timestamp: float = field(default_factory=time.time)
    files: dict[str, FileSnapshot] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class AutoHealingManager:
    """Orchestrates automatic snapshot capture, rollbacks, and recovery prompts."""

    def __init__(
        self,
        root_dir: Path | str = ".",
        max_snapshots: int = MAX_HEALING_SNAPSHOTS,
        enabled: bool = True,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.max_snapshots = max_snapshots
        self._enabled = enabled
        self._snapshots: list[HealingSnapshot] = []
        self._recent_errors: list[str] = []
        self._consecutive_failures: int = 0

    @property
    def is_enabled(self) -> bool:
        """Return True if auto-healing snapshots and rollbacks are enabled."""
        return self._enabled

    def enable(self) -> None:
        """Enable auto-healing snapshots and rollbacks."""
        self._enabled = True

    def disable(self) -> None:
        """Disable auto-healing snapshots and rollbacks."""
        self._enabled = False

    def take_snapshot(
        self,
        description: str = "Pre-step snapshot",
        iteration: int = 0,
        paths: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Capture filesystem snapshot before executing a step."""
        if not self._enabled:
            return ""

        snapshot_id = uuid.uuid4().hex[:8]
        snapshot = HealingSnapshot(
            id=snapshot_id,
            iteration=iteration,
            description=description,
            metadata=metadata or {},
        )
        self._snapshots.append(snapshot)

        if paths:
            for p in paths:
                self.record_file_before_mutation(p, snapshot_id=snapshot_id)

        if len(self._snapshots) > self.max_snapshots:
            self._snapshots.pop(0)

        return snapshot_id

    def record_file_before_mutation(
        self, rel_path: str, snapshot_id: str | None = None
    ) -> None:
        """Record the state of a file in the active or latest snapshot."""
        if not self._enabled:
            return

        target_snap: HealingSnapshot | None = None
        if snapshot_id:
            for s in self._snapshots:
                if s.id == snapshot_id:
                    target_snap = s
                    break
        else:
            target_snap = self._snapshots[-1] if self._snapshots else None

        if target_snap is None:
            return

        norm_path = Path(rel_path).as_posix()
        if norm_path in target_snap.files:
            return  # Already recorded in this snapshot

        full_path = (self.root_dir / norm_path).resolve()
        if not full_path.exists():
            target_snap.files[norm_path] = FileSnapshot(rel_path=norm_path, exists=False)
            return

        try:
            if full_path.stat().st_size > MAX_SNAPSHOT_FILE_BYTES:
                target_snap.files[norm_path] = FileSnapshot(
                    rel_path=norm_path, exists=True, is_binary=True
                )
                return

            text = full_path.read_text(encoding="utf-8", errors="replace")
            target_snap.files[norm_path] = FileSnapshot(
                rel_path=norm_path, exists=True, content=text
            )
        except OSError:
            target_snap.files[norm_path] = FileSnapshot(
                rel_path=norm_path, exists=True, is_binary=True
            )

    def record_failure(
        self, error: str, step_info: dict[str, Any] | None = None
    ) -> int:
        """Record an execution error and increment consecutive failure counter."""
        self._recent_errors.append(error)
        if len(self._recent_errors) > 10:
            self._recent_errors.pop(0)
        self._consecutive_failures += 1
        return self._consecutive_failures

    def reset_consecutive_failures(self) -> None:
        """Reset the failure counter following a successful step."""
        self._consecutive_failures = 0

    def get_recent_errors(self) -> list[str]:
        """Return list of recent error messages."""
        return list(self._recent_errors)

    def rollback_last(self) -> dict[str, Any]:
        """Roll back the most recent snapshot."""
        return self.rollback_to_snapshot(None)

    def rollback_to_snapshot(
        self, snapshot_id: str | None = None
    ) -> dict[str, Any]:
        """Revert all file mutations recorded in the specified snapshot."""
        if not self._snapshots:
            return {
                "success": False,
                "error": "No auto-healing snapshots available to restore",
                "reverted_files": [],
            }

        target_snap: HealingSnapshot | None = None
        if snapshot_id is None:
            target_snap = self._snapshots[-1]
        else:
            for s in self._snapshots:
                if s.id == snapshot_id:
                    target_snap = s
                    break

        if target_snap is None:
            return {
                "success": False,
                "error": f"Snapshot '{snapshot_id}' not found",
                "reverted_files": [],
            }

        reverted: list[str] = []
        errors: list[str] = []

        for rel_path, snap in target_snap.files.items():
            full_path = (self.root_dir / rel_path).resolve()
            try:
                if not snap.exists:
                    if full_path.exists():
                        full_path.unlink()
                        reverted.append(f"deleted {rel_path}")
                else:
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    if snap.content is not None:
                        full_path.write_text(snap.content, encoding="utf-8")
                        reverted.append(f"restored {rel_path}")
            except OSError as exc:
                errors.append(f"Failed to restore {rel_path}: {exc}")

        # Remove the restored snapshot from active list
        if target_snap in self._snapshots:
            self._snapshots.remove(target_snap)

        logger.info(
            "AutoHealing rollback performed on snapshot '%s': %d files reverted (%s)",
            target_snap.id,
            len(reverted),
            ", ".join(reverted) if reverted else "none",
        )

        return {
            "success": len(errors) == 0,
            "snapshot_id": target_snap.id,
            "description": target_snap.description,
            "reverted_files": reverted,
            "errors": errors,
        }

    def synthesize_recovery_prompt(
        self,
        drift_report: DriftReport | None = None,
        recent_errors: list[str] | None = None,
        goal: str = "",
    ) -> str:
        """Synthesize a structured recovery strategy prompt for the planner/reflector."""
        errors = recent_errors or self._recent_errors
        err_snippet = "\n".join(f"- {e}" for e in errors[-3:]) if errors else "- Unknown error"

        reasons = drift_report.reasons if drift_report else []
        reasons_snippet = "\n".join(f"- {r}" for r in reasons) if reasons else "- Plan execution drifted from primary goal."

        severity = drift_report.severity.value if drift_report else "critical"
        cycle_sig = drift_report.cycle_signature if drift_report else None

        guidelines = [
            "1. Do NOT repeat the exact same failed command, query, or file mutation.",
            "2. If a specific tool keeps failing, switch to an alternative tool or approach (e.g. read before edit, run tests sequentially, inspect diagnostics).",
            "3. Decompose complex multi-step operations into smaller, verifiable sub-steps.",
            "4. Verify preconditions before modifying files or executing commands.",
        ]

        if cycle_sig:
            guidelines.insert(
                0,
                f"🚨 BREAK CYCLE: An execution loop was detected on '{cycle_sig}'. You MUST avoid this action and choose an alternative path.",
            )

        prompt_lines = [
            f"=== AGENT SELF-HEALING RECOVERY GUIDANCE (Severity: {severity.upper()}) ===",
            f"Goal: {goal}" if goal else "",
            "",
            "Recent Failures / Root Causes:",
            err_snippet,
            "",
            "Drift & Cycle Diagnostics:",
            reasons_snippet,
            "",
            "Mandatory Recovery Strategy:",
            "\n".join(guidelines),
            "===============================================================",
        ]

        return "\n".join(line for line in prompt_lines if line is not None)

    def list_snapshots(self) -> list[dict[str, Any]]:
        """List summary of recorded snapshots."""
        return [
            {
                "id": s.id,
                "iteration": s.iteration,
                "description": s.description,
                "timestamp": s.timestamp,
                "file_count": len(s.files),
                "files": list(s.files.keys()),
            }
            for s in reversed(self._snapshots)
        ]

    def reset(self) -> None:
        """Clear all snapshots and failure records."""
        self._snapshots.clear()
        self._recent_errors.clear()
        self._consecutive_failures = 0


__all__ = [
    "AutoHealingManager",
    "HealingSnapshot",
]
