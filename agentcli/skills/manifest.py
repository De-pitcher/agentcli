"""Skill manifest and frontmatter parsing models for Phase 33."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SkillParameter:
    """Definition of a skill input parameter."""

    name: str
    type: str = "string"  # "string", "int", "float", "bool", "enum"
    default: Any = None
    description: str = ""
    options: list[str] = field(default_factory=list)
    required: bool = False

    def validate_and_cast(self, value: Any) -> Any:
        """Validate and cast an input value according to parameter type."""
        if value is None:
            if self.required and self.default is None:
                raise ValueError(f"Required parameter '{self.name}' is missing")
            return self.default

        if self.type == "int":
            try:
                return int(value)
            except (ValueError, TypeError) as err:
                raise ValueError(f"Parameter '{self.name}' must be an integer, got: {value}") from err

        if self.type == "float":
            try:
                return float(value)
            except (ValueError, TypeError) as err:
                raise ValueError(f"Parameter '{self.name}' must be a float, got: {value}") from err

        if self.type == "bool":
            if isinstance(value, bool):
                return value
            val_str = str(value).strip().lower()
            if val_str in ("true", "1", "yes", "y", "t"):
                return True
            if val_str in ("false", "0", "no", "n", "f"):
                return False
            raise ValueError(f"Parameter '{self.name}' must be a boolean, got: {value}")

        if self.type == "enum":
            val_str = str(value).strip()
            if self.options and val_str not in self.options:
                raise ValueError(
                    f"Parameter '{self.name}' must be one of {self.options}, got: {val_str}"
                )
            return val_str

        return str(value)


@dataclass
class SkillStep:
    """Definition of an individual step in a multi-stage skill recipe."""

    id: str
    name: str
    goal: str
    tools: list[str] = field(default_factory=list)
    condition: str = ""  # Optional gating expression


@dataclass
class SkillManifest:
    """Complete structured definition of a skill loaded from a SKILL.md file."""

    name: str
    description: str
    version: str = "1.0.0"
    author: str = ""
    execution_mode: str = "loop"  # "chat", "loop", "subagent"
    max_iterations: int = 5
    parameters: dict[str, SkillParameter] = field(default_factory=dict)
    required_tools: list[str] = field(default_factory=list)
    prompt_template: str = ""
    steps: list[SkillStep] = field(default_factory=list)
    skill_dir: Path | None = None
    source_type: str = "project"  # "builtin", "project", "user"

    def render_prompt(self, arguments: dict[str, Any], context: dict[str, Any] | None = None) -> str:
        """Render skill prompt template with validated arguments and dynamic context."""
        merged: dict[str, Any] = {}

        # 1. Fill parameter defaults & validated values
        for param_name, param_def in self.parameters.items():
            user_val = arguments.get(param_name, param_def.default)
            merged[param_name] = param_def.validate_and_cast(user_val)

        # 2. Inject extra arguments not defined in parameters
        for k, v in arguments.items():
            if k not in merged:
                merged[k] = v

        # 3. Inject context variables (e.g. workspace, active branch, date)
        if context:
            for ck, cv in context.items():
                if ck not in merged:
                    merged[ck] = cv

        rendered = self.prompt_template
        for key, val in merged.items():
            pattern = re.compile(rf"\{{\{{\s*{re.escape(key)}\s*\}}\}}")
            val_str = str(val)

            def _make_repl(text: str) -> Any:
                return lambda _m: text

            rendered = pattern.sub(_make_repl(val_str), rendered)

        return rendered.strip()

    def to_dict(self) -> dict[str, Any]:
        """Convert manifest to serializable summary dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "execution_mode": self.execution_mode,
            "max_iterations": self.max_iterations,
            "parameters": {
                k: {
                    "type": p.type,
                    "default": p.default,
                    "description": p.description,
                    "options": p.options,
                    "required": p.required,
                }
                for k, p in self.parameters.items()
            },
            "required_tools": self.required_tools,
            "source_type": self.source_type,
            "skill_dir": str(self.skill_dir) if self.skill_dir else None,
        }


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Lightweight zero-dependency YAML parser for skill frontmatter."""
    result: dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    current_key: str | None = None
    sub_key: str | None = None

    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.strip()
        i += 1

        if not line or line.startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip())

        # Top-level key
        if indent == 0 and ":" in line:
            parts = line.split(":", 1)
            key = parts[0].strip()
            val_str = parts[1].strip()
            current_key = key
            sub_key = None

            if not val_str:
                result[key] = {}
            elif val_str.startswith("[") and val_str.endswith("]"):
                items = [x.strip().strip("'\"") for x in val_str[1:-1].split(",") if x.strip()]
                result[key] = items
            else:
                result[key] = _cast_scalar(val_str)
        # Indented dict or parameter child
        elif indent == 2 and current_key is not None:
            if line.startswith("- "):
                if not isinstance(result.get(current_key), list):
                    result[current_key] = []
                result[current_key].append(_cast_scalar(line[2:].strip()))
            else:
                if not isinstance(result.get(current_key), dict):
                    result[current_key] = {}
                parts = line.split(":", 1)
                sub_name = parts[0].strip()
                sub_val_str = parts[1].strip() if len(parts) > 1 else ""

                if not sub_val_str:
                    result[current_key][sub_name] = {}
                    sub_key = sub_name
                elif sub_val_str.startswith("[") and sub_val_str.endswith("]"):
                    items = [x.strip().strip("'\"") for x in sub_val_str[1:-1].split(",") if x.strip()]
                    result[current_key][sub_name] = items
                    sub_key = None
                else:
                    result[current_key][sub_name] = _cast_scalar(sub_val_str)
                    sub_key = None
        elif indent == 4 and current_key is not None and sub_key is not None:
            if line.startswith("- "):
                if not isinstance(result[current_key].get(sub_key), list):
                    result[current_key][sub_key] = []
                result[current_key][sub_key].append(_cast_scalar(line[2:].strip()))
            elif isinstance(result[current_key], dict) and isinstance(result[current_key].get(sub_key), dict):
                parts = line.split(":", 1)
                field_name = parts[0].strip()
                field_val_str = parts[1].strip() if len(parts) > 1 else ""
                if field_val_str.startswith("[") and field_val_str.endswith("]"):
                    items = [x.strip().strip("'\"") for x in field_val_str[1:-1].split(",") if x.strip()]
                    result[current_key][sub_key][field_name] = items
                else:
                    result[current_key][sub_key][field_name] = _cast_scalar(field_val_str)

    return result


def _cast_scalar(val: str) -> Any:
    cleaned = val.strip().strip("'\"")
    if cleaned.lower() == "true":
        return True
    if cleaned.lower() == "false":
        return False
    if cleaned.lower() == "null" or cleaned.lower() == "none" or cleaned == "":
        return None
    try:
        return int(cleaned)
    except ValueError:
        pass
    try:
        return float(cleaned)
    except ValueError:
        pass
    return cleaned


def parse_skill_markdown(content: str, source_path: Path | None = None, source_type: str = "project") -> SkillManifest:
    """Parse a SKILL.md file with YAML frontmatter into a SkillManifest."""
    frontmatter_match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n(.*)$", content, re.DOTALL)
    if not frontmatter_match:
        # Fallback: entire file is prompt template
        name = source_path.parent.name if source_path else "custom-skill"
        return SkillManifest(
            name=name,
            description=f"Custom skill '{name}'",
            prompt_template=content.strip(),
            skill_dir=source_path.parent if source_path else None,
            source_type=source_type,
        )

    fm_raw, body_raw = frontmatter_match.group(1), frontmatter_match.group(2)
    meta = parse_simple_yaml(fm_raw)

    name = str(meta.get("name") or (source_path.parent.name if source_path else "unnamed-skill"))
    description = str(meta.get("description") or "")
    version = str(meta.get("version") or "1.0.0")
    author = str(meta.get("author") or "")
    execution_mode = str(meta.get("execution_mode") or "loop")
    max_iterations = int(meta.get("max_iterations") or 5)
    required_tools = list(meta.get("required_tools") or [])

    parameters: dict[str, SkillParameter] = {}
    params_dict = meta.get("parameters") or {}
    if isinstance(params_dict, dict):
        for p_name, p_data in params_dict.items():
            if isinstance(p_data, dict):
                parameters[p_name] = SkillParameter(
                    name=p_name,
                    type=str(p_data.get("type", "string")),
                    default=p_data.get("default"),
                    description=str(p_data.get("description", "")),
                    options=list(p_data.get("options", [])),
                    required=bool(p_data.get("required", False)),
                )
            else:
                parameters[p_name] = SkillParameter(
                    name=p_name,
                    default=p_data,
                )

    return SkillManifest(
        name=name,
        description=description,
        version=version,
        author=author,
        execution_mode=execution_mode,
        max_iterations=max_iterations,
        parameters=parameters,
        required_tools=required_tools,
        prompt_template=body_raw.strip(),
        skill_dir=source_path.parent if source_path else None,
        source_type=source_type,
    )
