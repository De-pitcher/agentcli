"""Task evaluator and result schemas for AgentCLI Arena."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentcli.arena.task import BenchmarkTask


@dataclass
class TaskResult:
    """Evaluation result for an individual benchmark task execution."""
    task_id: str
    task_title: str
    model: str
    success: bool
    exit_reason: str
    latency_seconds: float = 0.0
    turns_count: int = 0
    tool_calls_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    error_message: str | None = None
    verification_stdout: str = ""
    diff_patch: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert task result to dictionary."""
        return {
            "task_id": self.task_id,
            "task_title": self.task_title,
            "model": self.model,
            "success": self.success,
            "exit_reason": self.exit_reason,
            "latency_seconds": round(self.latency_seconds, 3),
            "turns_count": self.turns_count,
            "tool_calls_count": self.tool_calls_count,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "error_message": self.error_message,
            "verification_stdout": self.verification_stdout,
            "diff_patch": self.diff_patch,
            "metadata": self.metadata,
        }


class TaskEvaluator:
    """Evaluates agent-generated workspaces against benchmark task verification hooks."""

    def __init__(self, python_executable: str | None = None) -> None:
        self.python_executable = python_executable or sys.executable

    def evaluate(
        self,
        task: BenchmarkTask,
        workspace_dir: Path,
        verification_timeout: int = 30,
    ) -> tuple[bool, str, str]:
        """
        Evaluate task against expected files and verification test commands.
        
        Returns:
            (success, exit_reason, verification_stdout)
        """
        # 1. Verify expected files and regex patterns
        for rel_path, pattern in task.expected_files.items():
            target_file = workspace_dir / rel_path
            if not target_file.exists():
                return False, "file_missing", f"Expected file not found: {rel_path}"
            if pattern:
                try:
                    content = target_file.read_text(encoding="utf-8", errors="replace")
                    if not re.search(pattern, content):
                        return False, "file_pattern_mismatch", f"File {rel_path} does not match expected pattern: {pattern}"
                except Exception as e:  # noqa: BLE001
                    return False, "file_read_error", f"Could not read {rel_path}: {e}"

        # 2. Inject verification test files if provided
        for test_rel_path, test_content in task.test_files.items():
            test_target = workspace_dir / test_rel_path
            test_target.parent.mkdir(parents=True, exist_ok=True)
            test_target.write_text(test_content, encoding="utf-8")

        # 3. Execute test command if defined
        if task.test_command:
            cmd = task.test_command
            # Replace python command with current python interpreter if needed
            if cmd.startswith("python "):
                cmd = f'"{self.python_executable}" {cmd[7:]}'
            elif cmd.startswith("pytest "):
                cmd = f'"{self.python_executable}" -m pytest {cmd[7:]}'

            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(workspace_dir),
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=verification_timeout,
                    check=False,
                )
                output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
                if proc.returncode != 0:
                    return False, "test_failure", output.strip()
                return True, "success", output.strip()
            except subprocess.TimeoutExpired:
                return False, "test_timeout", f"Test command timed out after {verification_timeout}s"
            except Exception as e:  # noqa: BLE001
                return False, "test_execution_error", f"Failed to execute test command: {e}"

        return True, "success", "All file assertions passed."
