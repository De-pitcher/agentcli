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
            "description": "Perform file operations: read, write, append, create, delete, list, mkdir, or exists/stat. Paths are constrained to the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "read",
                            "write",
                            "append",
                            "create",
                            "delete",
                            "list",
                            "mkdir",
                            "exists",
                            "stat",
                        ],
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
            "description": "Inspect git status, search for files, search code contents, list directory tree, or manage git branches and isolated worktrees across the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "git_status",
                            "search_files",
                            "search_code",
                            "list_tree",
                            "git_branch",
                            "git_worktree",
                        ],
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
                    "branch_name": {
                        "type": "string",
                        "description": "Branch name for git_branch or git_worktree operations",
                    },
                    "worktree_path": {
                        "type": "string",
                        "description": "Destination path for git_worktree operations",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["create", "remove", "delete", "checkout", "list"],
                        "description": "Action for git_branch or git_worktree operations",
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
    SubAgentType.WEB_FETCH.value: {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch and read web pages, online documentation, GitHub PRs/issues, or API specifications and convert to clean Markdown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The HTTP or HTTPS URL to fetch",
                    },
                    "content_offset": {
                        "type": "integer",
                        "description": "Byte offset into the document content (default: 0)",
                    },
                    "max_bytes": {
                        "type": "integer",
                        "description": "Maximum bytes to return (default: 50000)",
                    },
                    "raw": {
                        "type": "boolean",
                        "description": "If true, returns raw response body without HTML-to-markdown conversion",
                    },
                },
                "required": ["url"],
            },
        },
    },
    SubAgentType.GREP_SEARCH.value: {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "High-speed regex and text search across files in the workspace with line numbers and snippets. Accelerated by ripgrep if available.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term or regex pattern",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file path to search within (default: current workspace)",
                    },
                    "is_regex": {
                        "type": "boolean",
                        "description": "If true, treats query as a regular expression pattern",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "If true, performs case-sensitive matching",
                    },
                    "includes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Glob patterns of files to include (e.g. ['*.py', '*.toml'])",
                    },
                    "excludes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Glob patterns of files to exclude",
                    },
                    "match_per_line": {
                        "type": "boolean",
                        "description": "If true, returns line numbers and line content for each match; if false, returns matching filenames",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matches to return (default: 50)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    SubAgentType.CONSENSUS.value: {
        "type": "function",
        "function": {
            "name": "consensus",
            "description": "Evaluate multi-agent votes or debate results and calculate consensus verdicts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "votes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "voter_id": {"type": "string"},
                                "choice": {"type": "string"},
                                "confidence": {"type": "number"},
                                "rationale": {"type": "string"},
                            },
                            "required": ["choice"],
                        },
                        "description": "List of agent votes to evaluate",
                    },
                    "strategy": {
                        "type": "string",
                        "enum": ["majority", "supermajority", "unanimous", "weighted", "plurality"],
                        "default": "majority",
                        "description": "Consensus voting strategy",
                    },
                    "min_threshold": {
                        "type": "number",
                        "description": "Minimum threshold ratio for consensus approval (default: 0.5)",
                    },
                },
                "required": ["votes"],
            },
        },
    },
    SubAgentType.TASK_MANAGER.value: {
        "type": "function",
        "function": {
            "name": "manage_task",
            "description": "Manage background processes and daemon tasks: run/start, list, status, logs, send_input, or kill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["run", "list", "status", "logs", "send_input", "kill"],
                        "description": "Action to perform on background task(s)",
                    },
                    "command": {
                        "type": "string",
                        "description": "Command to run in the background (required for action='run')",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Task ID to inspect, send input to, or kill (e.g. 'task_1')",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for the background task",
                    },
                    "input": {
                        "type": "string",
                        "description": "Input text to write to stdin of the task (for action='send_input')",
                    },
                    "tail": {
                        "type": "integer",
                        "description": "Number of recent log lines to retrieve (default: 50)",
                    },
                },
                "required": ["action"],
            },
        },
    },
    SubAgentType.ASK_QUESTION.value: {
        "type": "function",
        "function": {
            "name": "ask_question",
            "description": "Prompt the user for clarification, confirmation, or to select from multiple proposed options.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Clarifying question or prompt to present to the user",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of multiple choice options for the user",
                    },
                    "is_multi_select": {
                        "type": "boolean",
                        "description": "Whether the user can select multiple options (default: false)",
                    },
                    "context": {
                        "type": "string",
                        "description": "Additional context or background explaining why clarification is needed",
                    },
                },
                "required": ["question"],
            },
        },
    },
    SubAgentType.DIAGNOSTICS_CHECK.value: {
        "type": "function",
        "function": {
            "name": "diagnostics_check",
            "description": "Run linters, compilers, or test suites and extract structured diagnostic error spans for automated self-repair.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Command to run and check diagnostics for (e.g. 'pytest', 'ruff check .', 'mypy .')",
                    },
                    "output": {
                        "type": "string",
                        "description": "Raw compiler/linter output text to parse directly without running a subprocess",
                    },
                    "framework": {
                        "type": "string",
                        "enum": ["auto", "pytest", "ruff", "mypy", "tsc", "eslint", "cargo"],
                        "default": "auto",
                        "description": "Framework parser format hint",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory",
                    },
                },
            },
        },
    },
    SubAgentType.SKILL_RUNNER.value: {
        "type": "function",
        "function": {
            "name": "skill_runner",
            "description": "Discover, inspect, and execute custom skills and multi-stage workflow recipes from .agentcli/skills/",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "info", "run", "reload"],
                        "description": "Action to perform: 'list' available skills, 'info' on a skill, 'run' a skill recipe, or 'reload' skills from disk",
                    },
                    "name": {
                        "type": "string",
                        "description": "Skill name (required for 'info' and 'run' actions)",
                    },
                    "args": {
                        "type": "object",
                        "description": "Input parameters dict passed to the skill prompt template",
                    },
                },
                "required": ["action"],
            },
        },
    },
    "worktree": {
        "type": "function",
        "function": {
            "name": "worktree",
            "description": "Create, list, diff, merge, and discard isolated Git worktrees for safe sandboxed development and refactoring",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "status", "diff", "merge", "discard", "prune"],
                        "description": "Action to perform: create sandbox worktree, list active worktrees, check status/dirty files, show unified diff, merge to base branch, discard worktree, or prune stale checkouts",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch name for the worktree sandbox (required for create, status, diff, merge, discard)",
                    },
                    "base_ref": {
                        "type": "string",
                        "description": "Base branch or commit to branch off (defaults to current branch/HEAD)",
                    },
                    "strategy": {
                        "type": "string",
                        "enum": ["squash", "merge"],
                        "description": "Merge strategy when merging worktree back into target branch (default: squash)",
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Commit message for squash merge or auto-commit",
                    },
                    "delete_branch": {
                        "type": "boolean",
                        "description": "Whether to delete the git branch when discarding worktree (default: false)",
                    },
                },
                "required": ["action"],
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
