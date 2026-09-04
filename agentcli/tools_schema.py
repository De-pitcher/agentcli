"""Tool schema definitions for OpenAI/OpenRouter function calling format."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .subagents.base import SubAgentType

TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    SubAgentType.FILE_OPS.value: {
        "type": "function",
        "function": {
            "name": "file_ops",
            "description": "Perform file operations: read, write, create, delete, list, or mkdir. Paths are constrained to the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["read", "write", "create", "delete", "list", "mkdir"],
                        "description": "The file operation to perform",
                    },
                    "path": {
                        "type": "string",
                        "description": "Path to file or directory",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write (required for write operation)",
                    },
                    "encoding": {
                        "type": "string",
                        "description": "Text encoding (default: utf-8)",
                    },
                },
                "required": ["operation", "path"],
            },
        },
    },
    SubAgentType.SHELL_EXECUTION.value: {
        "type": "function",
        "function": {
            "name": "shell_execution",
            "description": "Execute sandboxed shell commands safely. Direct binary execution without shell=True.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Command to execute (e.g. 'pytest tests/' or 'git status')",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in seconds (default: 30)",
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Working directory (default: current directory)",
                    },
                },
                "required": ["command"],
            },
        },
    },
    SubAgentType.CODE_ANALYZER.value: {
        "type": "function",
        "function": {
            "name": "code_analyzer",
            "description": "Analyze code files for bugs, security issues, performance problems, and style.",
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of file paths to analyze",
                    },
                    "focus": {
                        "type": "string",
                        "enum": ["security", "performance", "style", "general"],
                        "default": "general",
                        "description": "Focus of the analysis",
                    },
                    "context": {
                        "type": "string",
                        "description": "Additional context for the analysis",
                    },
                },
                "required": ["files"],
            },
        },
    },
    SubAgentType.WEB_SEARCH.value: {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for up-to-date information, documentation, or answers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 10,
                        "description": "Maximum number of search results to return",
                    },
                    "provider": {
                        "type": "string",
                        "enum": ["brave", "duckduckgo"],
                        "description": "Search provider to use",
                    },
                },
                "required": ["query"],
            },
        },
    },
    SubAgentType.WORKSPACE.value: {
        "type": "function",
        "function": {
            "name": "workspace",
            "description": "Inspect git status, search for files, search code contents, or list directory tree across the workspace repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["git_status", "search_files", "search_code", "list_tree"],
                        "description": "Operation to perform",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query for search_code",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Filename pattern/glob for search_files",
                    },
                    "path": {
                        "type": "string",
                        "description": "Target root directory path (default: current workspace)",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Max depth for list_tree (default: 2)",
                    },
                },
                "required": ["operation"],
            },
        },
    },
}


def get_tool_definitions(
    allowed_types: Iterable[SubAgentType | str] | None = None,
) -> list[dict[str, Any]]:
    """Return OpenAI/OpenRouter function calling tool definitions for the allowed agent types."""
    if allowed_types is None:
        return list(TOOL_DEFINITIONS.values())

    allowed_names: set[str] = set()
    for item in allowed_types:
        if isinstance(item, SubAgentType):
            allowed_names.add(item.value)
        elif isinstance(item, str):
            allowed_names.add(item)

    return [definition for name, definition in TOOL_DEFINITIONS.items() if name in allowed_names]
