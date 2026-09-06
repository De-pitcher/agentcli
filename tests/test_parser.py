from __future__ import annotations

import json

import pytest

from agentcli.parser import extract_json_payload, repair_json_syntax, robust_json_loads


def test_extract_json_payload_direct() -> None:
    """Test extract_json_payload directly with various markdown and text wrappers."""
    raw = "Here is the result:\n```json\n{\"key\": \"val\"}\n```\nDone."
    assert extract_json_payload(raw) == '{"key": "val"}'

    unfenced = "Conversational preamble {\"a\": 1} conversational trailer"
    assert extract_json_payload(unfenced) == '{"a": 1}'

    empty = extract_json_payload("")
    assert empty == ""


def test_repair_json_syntax_direct() -> None:
    """Test repair_json_syntax directly with trailing commas and python literals."""
    broken = "{'a': True, 'b': False, 'c': None, 'd': [1, 2, ], }"
    repaired = repair_json_syntax(broken)
    assert '"a": true' in repaired
    assert '"b": false' in repaired
    assert '"c": null' in repaired
    assert "[1, 2]" in repaired


def test_standard_json_loads() -> None:
    """Test standard JSON parsing unchanged."""
    valid_json = '{"name": "agentcli", "version": 2, "enabled": true}'
    parsed = robust_json_loads(valid_json)
    assert parsed == {"name": "agentcli", "version": 2, "enabled": True}


def test_markdown_fenced_json() -> None:
    """Test JSON enclosed in ```json ... ``` code blocks."""
    fenced = """
Here is the execution plan:
```json
[
  {"agent_type": "file_ops", "payload": {"path": "src/main.py"}},
  {"agent_type": "shell", "payload": {"command": "pytest"}}
]
```
Let me know if you need changes.
"""
    parsed = robust_json_loads(fenced)
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    assert parsed[0]["agent_type"] == "file_ops"
    assert parsed[1]["payload"]["command"] == "pytest"


def test_markdown_fenced_without_language_tag() -> None:
    """Test JSON enclosed in generic ``` ... ``` blocks."""
    fenced = """
```
{"decision": "REPLAN", "reason": "Missing imports in test file"}
```
"""
    parsed = robust_json_loads(fenced)
    assert parsed["decision"] == "REPLAN"
    assert "Missing imports" in parsed["reason"]


def test_trailing_commas_in_objects_and_arrays() -> None:
    """Test auto-repair of trailing commas in JSON structures."""
    malformed = """
    {
      "files": ["src/app.py", "src/auth.py", ],
      "options": {
         "strict": true,
         "timeout": 30,
      },
    }
    """
    parsed = robust_json_loads(malformed)
    assert len(parsed["files"]) == 2
    assert parsed["options"]["timeout"] == 30


def test_python_boolean_and_none_literals() -> None:
    """Test auto-repair of Python True, False, None to valid JSON."""
    py_literal = '{"success": True, "active": False, "error": None, "priority": 10}'
    parsed = robust_json_loads(py_literal)
    assert parsed["success"] is True
    assert parsed["active"] is False
    assert parsed["error"] is None
    assert parsed["priority"] == 10


def test_single_quoted_keys_and_values() -> None:
    """Test auto-repair of single quotes in JSON payloads."""
    single_quoted = "{'agent_type': 'code_analyzer', 'focus': 'security', 'count': 5}"
    parsed = robust_json_loads(single_quoted)
    assert parsed["agent_type"] == "code_analyzer"
    assert parsed["focus"] == "security"
    assert parsed["count"] == 5


def test_conversational_text_wrapping() -> None:
    """Test extraction when JSON is surrounded by conversational filler without code fences."""
    text = """
    I have analyzed the request and determined the following steps:
    [
      {"agent_type": "file_ops", "goal_criterion": "main.py"},
      {"agent_type": "shell_execution", "goal_criterion": "done"}
    ]
    Please execute these sequentially.
    """
    parsed = robust_json_loads(text)
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    assert parsed[0]["goal_criterion"] == "main.py"


def test_empty_or_invalid_string_raises() -> None:
    """Test that truly invalid or empty strings raise JSONDecodeError."""
    with pytest.raises(json.JSONDecodeError):
        robust_json_loads("")

    with pytest.raises(json.JSONDecodeError):
        robust_json_loads("This is purely conversational text with no JSON braces or brackets at all.")
