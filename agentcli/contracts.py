"""Canonical tool contracts and conservative schema validation.

External schemas outside this supported subset are rejected at registration.
"""

from __future__ import annotations

import copy
import math
import os
import shlex
from dataclasses import dataclass
from typing import Any

_SUPPORTED = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "description",
    "default",
    "title",
    "$schema",
    "const",
    "anyOf",
}


def check_schema(schema: dict[str, Any]) -> None:
    if not isinstance(schema, dict) or set(schema) - _SUPPORTED:
        raise ValueError("Unsupported tool schema; registration refused")
    kind = schema.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    if not kinds or any(
        k
        not in {
            "object",
            "array",
            "string",
            "integer",
            "number",
            "boolean",
            "null",
        }
        for k in kinds
    ):
        raise ValueError("Tool schema must declare a supported type")
    for child in schema.get("properties", {}).values():
        check_schema(child)
    if "items" in schema:
        check_schema(schema["items"])
    if "anyOf" in schema:
        branches = schema["anyOf"]
        if not isinstance(branches, list) or not 1 <= len(branches) <= 32:
            raise ValueError("Invalid schema alternatives")
        for branch in branches:
            check_schema(branch)


def validate(value: Any, schema: dict[str, Any], location: str = "arguments") -> None:
    kind = schema["type"]
    if isinstance(kind, list):
        for alternative in kind:
            try:
                validate(value, {**schema, "type": alternative}, location)
                return
            except ValueError:
                continue
        raise ValueError(f"Invalid {location} type")
    valid = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "null": value is None,
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }[kind]
    if not valid:
        raise ValueError(f"{location} must be {kind}")
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise ValueError(f"Invalid {location} constant")
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            try:
                validate(value, branch, location)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"No supported {location} operation matches its required fields")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"Invalid {location}")
    if kind == "object":
        props = schema.get("properties", {})
        if set(schema.get("required", [])) - value.keys():
            raise ValueError(f"Missing required {location} fields")
        if schema.get("additionalProperties") is False and value.keys() - props.keys():
            raise ValueError(f"Unknown {location} fields")
        for name, child in props.items():
            if name in value:
                validate(value[name], child, f"{location}.{name}")
    elif kind == "array":
        for item in value:
            if "items" in schema:
                validate(item, schema["items"], location + "[]")
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 1000):
            raise ValueError(f"Invalid {location} length")
    elif kind == "string":
        if len(value) < schema.get("minLength", 0) or len(value.encode("utf-8")) > schema.get(
            "maxLength", 1048576
        ):
            raise ValueError(f"Invalid {location} length")
    elif kind in {"integer", "number"}:
        if (
            not math.isfinite(value)
            or value < schema.get("minimum", -math.inf)
            or value > schema.get("maximum", math.inf)
        ):
            raise ValueError(f"Invalid {location} number")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]
    effect: str = "unknown"
    builtin: bool = False

    def canonical(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Trusted operation aliases; arguments cannot declare their own effects."""
        result = dict(payload)
        if not self.builtin:
            return result
        field = "operation" if self.name in {"file_ops", "workspace"} else "action"
        defaults = {"workspace": "git_status", "worktree": "list", "task_manager": "list"}
        value = result.get(field, defaults.get(self.name))
        if isinstance(value, str):
            value = value.lower()
            aliases = {
                "task_manager": {
                    "start": "run",
                    "run_background": "run",
                    "input": "send_input",
                    "log": "logs",
                    "stop": "kill",
                    "terminate": "kill",
                },
                "worktree": {
                    "add": "create",
                    "new": "create",
                    "info": "status",
                    "remove": "discard",
                    "delete": "discard",
                },
            }
            result[field] = aliases.get(self.name, {}).get(value, value)
        if self.name == "task_manager" and "working_dir" in result:
            result.setdefault("cwd", result.pop("working_dir"))
        if self.name == "worktree" and "name" in result:
            result.setdefault("branch", result.pop("name"))
        if self.name == "workspace" and result.get("operation") in {"git_branch", "git_worktree"}:
            result.setdefault("action", "create")
        return result

    def operation(self, payload: dict[str, Any]) -> str:
        normalized = self.canonical(payload)
        return str(normalized.get("operation", normalized.get("action", "execute")))

    def effect_for(self, payload: dict[str, Any]) -> str:
        normalized = self.canonical(payload)
        if not self.builtin:
            return self.effect
        op = self.operation(normalized)
        if self.name == "file_ops":
            return (
                "mutation"
                if op in {"write", "append", "create", "delete", "mkdir"}
                else "observation"
            )
        if self.name == "task_manager":
            return "observation" if op in {"list", "status", "logs"} else "mutation"
        if self.name == "worktree":
            return "observation" if op in {"list", "status", "diff"} else "mutation"
        if self.name == "workspace":
            return (
                "mutation"
                if op in {"git_branch", "git_worktree"} and normalized.get("action") != "list"
                else "observation"
            )
        if self.name == "diagnostics_check":
            return "mutation" if normalized.get("command") else "observation"
        if self.name == "shell_execution":
            # These commands always use scoped in-process read shims, never PATH executables.
            try:
                parts = shlex.split(normalized.get("command", ""), posix=os.name != "nt")
            except ValueError:
                return "unknown"
            read_shims = {
                "pwd",
                "cwd",
                "echo",
                "whoami",
                "ls",
                "dir",
                "cat",
                "type",
                "which",
                "where",
            }
            return "observation" if parts and parts[0].lower() in read_shims else "mutation"
        return "observation"

    def native(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": copy.deepcopy(self.schema),
            },
        }

    def mcp(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": copy.deepcopy(self.schema),
        }
