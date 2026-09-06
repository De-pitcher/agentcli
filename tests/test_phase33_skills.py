"""Tests for Phase 33: Custom Skills, Workflow Recipes & Dynamic Slash Commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agentcli.agent.registry import ToolRegistry
from agentcli.skills.engine import SkillEngine
from agentcli.skills.loader import SkillLoader
from agentcli.skills.manifest import (
    SkillManifest,
    SkillParameter,
    parse_simple_yaml,
    parse_skill_markdown,
)
from agentcli.subagents.base import SubAgentTask, SubAgentType
from agentcli.subagents.skill_runner import SkillRunnerAgent
from agentcli.tools_schema import TOOL_DEFINITIONS, get_tool_definitions

# ---------------------------------------------------------------------------
# 1. Manifest Parsing Tests
# ---------------------------------------------------------------------------


def test_parse_skill_markdown_full(tmp_path: Path):
    skill_content = """---
# Comment header
name: sample-analysis
description: Performs sample analysis on codebase.
version: 1.2.0
author: Antigravity Team
tags: [analysis, test, automated]
parameters:
  target_dir:
    description: Target directory to analyze
    type: string
    required: true
    default: .
  max_depth:
    description: Max scanning depth
    type: int
    default: 3
  fast_mode:
    description: Skip detailed checks
    type: bool
    default: false
  simple_scalar: default_val
required_tools:
  - file_ops
  - shell_execution
---

# Sample Analysis Prompt

Analyze the directory: {{target_dir}} with depth {{max_depth}}.
Fast mode is {{fast_mode}}.
Workspace: {{workspace_dir}}
Branch: {{active_branch}}
"""
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(skill_content, encoding="utf-8")

    manifest = parse_skill_markdown(
        skill_content,
        source_path=skill_file,
        source_type="project",
    )

    assert manifest is not None
    assert manifest.name == "sample-analysis"
    assert manifest.description == "Performs sample analysis on codebase."
    assert manifest.version == "1.2.0"
    assert manifest.author == "Antigravity Team"
    assert manifest.source_type == "project"
    assert "target_dir" in manifest.parameters
    assert manifest.parameters["target_dir"].type == "string"
    assert manifest.parameters["target_dir"].required is True
    assert manifest.parameters["max_depth"].type == "int"
    assert manifest.parameters["max_depth"].default == 3
    assert manifest.parameters["fast_mode"].type == "bool"
    assert manifest.parameters["fast_mode"].default is False
    assert manifest.parameters["simple_scalar"].default == "default_val"
    assert manifest.required_tools == ["file_ops", "shell_execution"]
    assert manifest.prompt_template.startswith("# Sample Analysis Prompt")


def test_parse_skill_markdown_no_frontmatter(tmp_path: Path):
    skill_file = tmp_path / "custom-analysis" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    raw_prompt = "# Simple Prompt with no YAML header\nDo something useful."
    skill_file.write_text(raw_prompt, encoding="utf-8")

    manifest = parse_skill_markdown(raw_prompt, source_path=skill_file, source_type="project")
    assert manifest is not None
    assert manifest.name == "custom-analysis"
    assert manifest.prompt_template == raw_prompt


def test_manifest_cast_parameters():
    param_int = SkillParameter(name="count", type="int", default=10)
    assert param_int.validate_and_cast("42") == 42
    assert param_int.validate_and_cast(50) == 50
    with pytest.raises(ValueError):
        param_int.validate_and_cast("invalid")

    param_float = SkillParameter(name="score", type="float", default=0.5)
    assert param_float.validate_and_cast("0.85") == 0.85
    assert param_float.validate_and_cast(1.2) == 1.2
    with pytest.raises(ValueError):
        param_float.validate_and_cast("bad")

    param_bool = SkillParameter(name="flag", type="bool", default=False)
    assert param_bool.validate_and_cast(True) is True
    assert param_bool.validate_and_cast(False) is False
    assert param_bool.validate_and_cast("true") is True
    assert param_bool.validate_and_cast("yes") is True
    assert param_bool.validate_and_cast("1") is True
    assert param_bool.validate_and_cast("false") is False
    assert param_bool.validate_and_cast("0") is False
    with pytest.raises(ValueError):
        param_bool.validate_and_cast("not-a-bool")

    param_enum = SkillParameter(name="choice", type="enum", options=["low", "high"], default="low")
    assert param_enum.validate_and_cast("high") == "high"
    with pytest.raises(ValueError):
        param_enum.validate_and_cast("invalid")

    # None handling
    param_optional = SkillParameter(name="opt", default="default_opt")
    assert param_optional.validate_and_cast(None) == "default_opt"


def test_manifest_render_prompt():
    manifest = SkillManifest(
        name="test-skill",
        description="A test skill",
        parameters={
            "query": SkillParameter(name="query", type="string", default="default query"),
            "limit": SkillParameter(name="limit", type="int", default=10),
        },
        prompt_template="Searching for '{{query}}' with limit {{limit}} in {{workspace_dir}}.",
    )

    rendered = manifest.render_prompt(
        arguments={"query": "security bugs", "limit": 25},
        context={"workspace_dir": "/workspace"},
    )
    assert rendered == "Searching for 'security bugs' with limit 25 in /workspace."


def test_manifest_validation():
    manifest = SkillManifest(
        name="test-validation",
        description="Testing validation",
        parameters={
            "required_arg": SkillParameter(name="required_arg", type="string", required=True),
        },
        prompt_template="Execute with {{required_arg}}",
    )
    with pytest.raises(ValueError, match="Required parameter 'required_arg' is missing"):
        manifest.render_prompt(arguments={})

    rendered = manifest.render_prompt(arguments={"required_arg": "provided_value"})
    assert "provided_value" in rendered


def test_parse_simple_yaml_bracket_and_nested():
    yaml_text = """
title: Test Pipeline
tags: [fast, secure]
details:
  level: 4
  flags: [a, b]
"""
    res = parse_simple_yaml(yaml_text)
    assert res["title"] == "Test Pipeline"
    assert res["tags"] == ["fast", "secure"]
    assert res["details"]["level"] == 4
    assert res["details"]["flags"] == ["a", "b"]


# ---------------------------------------------------------------------------
# 2. Skill Loader Tests
# ---------------------------------------------------------------------------


def test_skill_loader_builtin_discovery():
    loader = SkillLoader()
    skills = loader.list_skills()

    assert len(skills) >= 3
    skill_names = [s.name for s in skills]
    assert "code-review" in skill_names
    assert "test-generator" in skill_names
    assert "security-audit" in skill_names

    assert loader.has_skill("code-review") is True
    assert loader.has_skill("non-existent-skill-xyz") is False

    code_review = loader.get_skill("code-review")
    assert code_review is not None
    assert code_review.source_type == "builtin"
    assert "target" in code_review.parameters


def test_skill_loader_tier_override(tmp_path: Path):
    # Setup mock workspace directory with .agentcli/skills/code-review/SKILL.md
    workspace_dir = tmp_path / "my_project"
    workspace_dir.mkdir()
    project_skills = workspace_dir / ".agentcli" / "skills" / "code-review"
    project_skills.mkdir(parents=True)

    override_skill = """---
name: code-review
description: Custom workspace overridden code review.
version: 2.0.0
---

Workspace custom code review template.
"""
    (project_skills / "SKILL.md").write_text(override_skill, encoding="utf-8")

    loader = SkillLoader(workspace_dir=workspace_dir)
    skill = loader.get_skill("code-review")

    assert skill is not None
    # Workspace version must override builtin
    assert skill.source_type == "project"
    assert skill.description == "Custom workspace overridden code review."
    assert skill.version == "2.0.0"


def test_skill_loader_user_and_extra_paths(tmp_path: Path):
    user_skills_dir = tmp_path / "user_home_skills"
    user_skills_dir.mkdir(parents=True)
    extra_dir = tmp_path / "extra_skills"
    extra_dir.mkdir(parents=True)

    (user_skills_dir / "user_skill").mkdir()
    (user_skills_dir / "user_skill" / "SKILL.md").write_text(
        "---\nname: user-skill\ndescription: User home skill\n---\nPrompt", encoding="utf-8"
    )

    (extra_dir / "extra_skill").mkdir()
    (extra_dir / "extra_skill" / "SKILL.md").write_text(
        "---\nname: extra-skill\ndescription: Extra skill\n---\nPrompt", encoding="utf-8"
    )

    loader = SkillLoader(
        workspace_dir=tmp_path / "empty_ws",
        user_skills_dir=user_skills_dir,
        extra_paths=[extra_dir],
    )

    assert loader.has_skill("user-skill") is True
    assert loader.has_skill("extra-skill") is True
    skill = loader.get_skill("user-skill")
    assert skill is not None
    assert skill.source_type == "user"


def test_skill_loader_direct_file_and_error_handling(tmp_path: Path):
    ws = tmp_path / "direct_ws"
    ws.mkdir()
    skills_dir = ws / ".agentcli" / "skills"
    skills_dir.mkdir(parents=True)

    # Put a direct SKILL.md file inside .agentcli/skills
    (skills_dir / "SKILL.md").write_text(
        "---\nname: direct-skill\ndescription: Directly in root\n---\nPrompt", encoding="utf-8"
    )

    loader = SkillLoader(workspace_dir=ws)
    assert loader.has_skill("direct-skill") is True


# ---------------------------------------------------------------------------
# 3. Skill Engine Tests
# ---------------------------------------------------------------------------


def test_skill_engine_list_and_prepare(tmp_path: Path):
    engine = SkillEngine(workspace_dir=tmp_path)
    skills = engine.list_available_skills()
    assert any(s["name"] == "code-review" for s in skills)

    manifest, rendered = engine.prepare_skill(
        "code-review",
        arguments={"target": "src/main.py", "strict": True},
        extra_context={"custom_var": "val123"},
    )
    assert manifest.name == "code-review"
    assert "src/main.py" in rendered


def test_skill_engine_git_branch_fallback(tmp_path: Path):
    engine = SkillEngine(workspace_dir=tmp_path)
    with patch("subprocess.run", side_effect=Exception("Git not found")):
        branch = engine._get_git_branch()
        assert branch == "main"


def test_skill_engine_prepare_nonexistent(tmp_path: Path):
    engine = SkillEngine(workspace_dir=tmp_path)
    with pytest.raises(KeyError, match="not found"):
        engine.prepare_skill("non-existent-skill")


# ---------------------------------------------------------------------------
# 4. Skill Runner Agent Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skill_runner_agent_list(tmp_path: Path):
    agent = SkillRunnerAgent(workspace_dir=str(tmp_path))
    task = SubAgentTask(
        agent_type=SubAgentType.SKILL_RUNNER,
        payload={"action": "list"},
    )
    result = await agent.run(task)

    assert result.success is True
    data = result.output
    assert "skills" in data
    assert "total" in data
    assert data["total"] >= 3
    assert any(s["name"] == "code-review" for s in data["skills"])


@pytest.mark.asyncio
async def test_skill_runner_agent_info(tmp_path: Path):
    agent = SkillRunnerAgent(workspace_dir=str(tmp_path))
    task = SubAgentTask(
        agent_type=SubAgentType.SKILL_RUNNER,
        payload={"action": "info", "name": "code-review"},
    )
    result = await agent.run(task)

    assert result.success is True
    data = result.output
    assert "skill" in data
    assert data["skill"]["name"] == "code-review"
    assert "parameters" in data["skill"]
    assert "target" in data["skill"]["parameters"]


@pytest.mark.asyncio
async def test_skill_runner_agent_info_not_found(tmp_path: Path):
    agent = SkillRunnerAgent(workspace_dir=str(tmp_path))
    task = SubAgentTask(
        agent_type=SubAgentType.SKILL_RUNNER,
        payload={"action": "info", "name": "unknown-skill"},
    )
    result = await agent.run(task)

    assert result.success is False
    assert result.error is not None
    assert "Skill 'unknown-skill' not found" in result.error


@pytest.mark.asyncio
async def test_skill_runner_agent_run(tmp_path: Path):
    agent = SkillRunnerAgent(workspace_dir=str(tmp_path))
    task = SubAgentTask(
        agent_type=SubAgentType.SKILL_RUNNER,
        payload={
            "action": "run",
            "name": "code-review",
            "args": {"target": "agentcli/skills/engine.py"},
        },
    )
    result = await agent.run(task)

    assert result.success is True
    data = result.output
    assert data["skill_name"] == "code-review"
    assert "rendered_prompt" in data
    assert "agentcli/skills/engine.py" in data["rendered_prompt"]


@pytest.mark.asyncio
async def test_skill_runner_agent_reload(tmp_path: Path):
    agent = SkillRunnerAgent(workspace_dir=str(tmp_path))
    task = SubAgentTask(
        agent_type=SubAgentType.SKILL_RUNNER,
        payload={"action": "reload"},
    )
    result = await agent.run(task)

    assert result.success is True
    assert result.output["reloaded"] is True


@pytest.mark.asyncio
async def test_skill_runner_agent_unknown_action(tmp_path: Path):
    agent = SkillRunnerAgent(workspace_dir=str(tmp_path))
    task = SubAgentTask(
        agent_type=SubAgentType.SKILL_RUNNER,
        payload={"action": "invalid_action_name"},
    )
    result = await agent.run(task)

    assert result.success is False
    assert result.error is not None
    assert "Unknown skill_runner action" in result.error


# ---------------------------------------------------------------------------
# 5. Tool Registry and Schema Tests
# ---------------------------------------------------------------------------


def test_tool_definitions_has_skill_runner():
    assert "skill_runner" in TOOL_DEFINITIONS
    skill_tool = TOOL_DEFINITIONS["skill_runner"]
    assert "action" in skill_tool["function"]["parameters"]["properties"]
    assert "name" in skill_tool["function"]["parameters"]["properties"]
    assert "args" in skill_tool["function"]["parameters"]["properties"]

    # Test get_tool_definitions with SubAgentType
    tool_list = get_tool_definitions([SubAgentType.SKILL_RUNNER])
    assert len(tool_list) == 1
    assert tool_list[0]["function"]["name"] == "skill_runner"

    # Test get_tool_definitions with str
    tool_list_str = get_tool_definitions(["skill_runner"])
    assert len(tool_list_str) == 1
    assert tool_list_str[0]["function"]["name"] == "skill_runner"

    # Test get_tool_definitions with None (all tools)
    all_tools = get_tool_definitions(None)
    assert len(all_tools) >= 1


def test_tool_registry_includes_skill_runner():
    registry = ToolRegistry()
    assert "skill_runner" in registry.registered_types()
    assert SubAgentType.SKILL_RUNNER.value == "skill_runner"
