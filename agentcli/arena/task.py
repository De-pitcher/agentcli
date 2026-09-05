"""Benchmark task definitions and schemas for the AgentCLI Arena."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskCategory(str, Enum):
    """Categories of benchmark developer tasks."""
    CODE_GEN = "code_gen"
    BUG_FIX = "bug_fix"
    REFACTOR = "refactor"
    TOOL_USE = "tool_use"
    MULTI_FILE = "multi_file"
    MESH_ORCHESTRATION = "mesh_orchestration"


@dataclass
class BenchmarkTask:
    """A standardized developer benchmark task."""
    id: str
    title: str
    category: TaskCategory
    description: str
    prompt: str
    workspace_setup: dict[str, str] = field(default_factory=dict)
    expected_files: dict[str, str] = field(default_factory=dict)
    test_command: str | None = None
    test_files: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 60
    max_iterations: int = 6
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert benchmark task to dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category.value,
            "description": self.description,
            "prompt": self.prompt,
            "workspace_setup": self.workspace_setup,
            "expected_files": self.expected_files,
            "test_command": self.test_command,
            "test_files": self.test_files,
            "timeout_seconds": self.timeout_seconds,
            "max_iterations": self.max_iterations,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BenchmarkTask:
        """Create a benchmark task from dictionary."""
        category_str = data.get("category", "code_gen")
        try:
            category = TaskCategory(category_str)
        except ValueError:
            category = TaskCategory.CODE_GEN

        return cls(
            id=data["id"],
            title=data.get("title", data["id"]),
            category=category,
            description=data.get("description", ""),
            prompt=data["prompt"],
            workspace_setup=data.get("workspace_setup", {}),
            expected_files=data.get("expected_files", {}),
            test_command=data.get("test_command"),
            test_files=data.get("test_files", {}),
            timeout_seconds=int(data.get("timeout_seconds", 60)),
            max_iterations=int(data.get("max_iterations", 6)),
            tags=list(data.get("tags", [])),
        )
