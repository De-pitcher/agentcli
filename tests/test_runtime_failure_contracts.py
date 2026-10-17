"""Targeted regression coverage for runtime failure contracts.

Covers:
1. Canonical tool contracts and conservative schema validation (agentcli.contracts)
2. Bounded execution results and envelope budget truncation (agentcli.bounds)
3. Direct worker-body filesystem operations and error invariants (agentcli._io_worker)
4. Runtime policy rejection, capabilities, and environment sanitization (agentcli.policy)
"""

from __future__ import annotations

import math
import re
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from agentcli import _io_worker, io_boundary
from agentcli.bounds import bound_result, read_text, scan_mtimes, walk_entries
from agentcli.contracts import ToolSpec, check_schema, validate
from agentcli.policy import PolicyDenied, RunPolicy
from agentcli.subagents.base import SubAgentResult, SubAgentType

# ============================================================================
# 1. Schema check & validation contracts (agentcli.contracts)
# ============================================================================


def test_contracts_check_schema_rejections() -> None:
    """check_schema must reject unsupported keywords, unknown types, and invalid anyOf structures."""
    # Unsupported keywords
    with pytest.raises(ValueError, match="Unsupported tool schema; registration refused"):
        check_schema({"type": "object", "patternProperties": {}})

    # Non-dict schema
    with pytest.raises(ValueError, match="Unsupported tool schema; registration refused"):
        check_schema("not-a-dict")  # type: ignore[arg-type]

    # Unsupported primitive type
    with pytest.raises(ValueError, match="Tool schema must declare a supported type"):
        check_schema({"type": "custom_type"})

    # Empty type list or invalid type in list
    with pytest.raises(ValueError, match="Tool schema must declare a supported type"):
        check_schema({"type": []})

    with pytest.raises(ValueError, match="Tool schema must declare a supported type"):
        check_schema({"type": ["string", "unknown_type"]})

    # Invalid anyOf branches: not a list or length out of [1, 32]
    with pytest.raises(ValueError, match="Invalid schema alternatives"):
        check_schema({"type": "object", "anyOf": "not-a-list"})

    with pytest.raises(ValueError, match="Invalid schema alternatives"):
        check_schema({"type": "object", "anyOf": []})

    with pytest.raises(ValueError, match="Invalid schema alternatives"):
        check_schema({"type": "object", "anyOf": [{"type": "string"}] * 33})

    # Recursive check on valid child properties and items
    check_schema(
        {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "items": {"type": "integer"},
            "anyOf": [{"type": "string"}, {"type": "integer"}],
        }
    )


def test_contracts_validate_type_alternatives_and_failures() -> None:
    """validate handles union type lists, rejecting if no alternative succeeds."""
    union_schema = {"type": ["string", "integer"]}
    # Valid alternative 1
    validate("test_val", union_schema)
    # Valid alternative 2
    validate(42, union_schema)
    # Neither string nor integer: boolean must not count as integer
    with pytest.raises(ValueError, match="Invalid arguments type"):
        validate(True, union_schema)
    with pytest.raises(ValueError, match="Invalid arguments type"):
        validate(3.14, union_schema)


def test_contracts_validate_const_and_anyof() -> None:
    """validate enforces const value & exact type identity and anyOf branches."""
    # Const enforcement: strict type check (True is not 1; value mismatch triggers invalid constant)
    const_schema = {"type": "number", "const": 1.0}
    validate(1.0, const_schema)
    with pytest.raises(ValueError, match="Invalid arguments constant"):
        validate(1, const_schema)
    with pytest.raises(ValueError, match="Invalid arguments constant"):
        validate(2.0, const_schema)

    # anyOf branch matching: succeeds if any branch passes, fails if none pass
    anyof_schema = {
        "type": "object",
        "anyOf": [
            {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}},
            {"type": "object", "required": ["b"], "properties": {"b": {"type": "integer"}}},
        ],
    }
    validate({"a": "hello"}, anyof_schema)
    validate({"b": 10}, anyof_schema)
    with pytest.raises(
        ValueError, match="No supported arguments operation matches its required fields"
    ):
        validate({"c": 1.23}, anyof_schema)


def test_contracts_validate_additional_properties_and_bounds() -> None:
    """validate rejects additional properties when disallowed and checks string/array/number bounds."""
    # additionalProperties: False
    obj_schema = {
        "type": "object",
        "properties": {"field": {"type": "string"}},
        "additionalProperties": False,
    }
    validate({"field": "ok"}, obj_schema)
    with pytest.raises(ValueError, match="Unknown arguments fields"):
        validate({"field": "ok", "unexpected": 123}, obj_schema)

    # Array length bounds
    arr_schema = {
        "type": "array",
        "items": {"type": "integer"},
        "minItems": 2,
        "maxItems": 4,
    }
    validate([1, 2], arr_schema)
    with pytest.raises(ValueError, match="Invalid items length"):
        validate([1], arr_schema, location="items")
    with pytest.raises(ValueError, match="Invalid items length"):
        validate([1, 2, 3, 4, 5], arr_schema, location="items")

    # String utf-8 byte length overflow
    str_schema = {"type": "string", "minLength": 2, "maxLength": 5}
    validate("abc", str_schema)
    with pytest.raises(ValueError, match="Invalid name length"):
        validate("a", str_schema, location="name")
    # Multibyte emoji exceeds 5 UTF-8 bytes (two 4-byte characters = 8 bytes)
    with pytest.raises(ValueError, match="Invalid name length"):
        validate("🎉✨", str_schema, location="name")

    # Numeric bounds and non-finite rejection
    num_schema = {"type": "number", "minimum": 0.0, "maximum": 10.0}
    validate(5.5, num_schema)
    with pytest.raises(ValueError, match="Invalid score number"):
        validate(-1.0, num_schema, location="score")
    with pytest.raises(ValueError, match="Invalid score number"):
        validate(15.0, num_schema, location="score")
    with pytest.raises(ValueError, match="Invalid score number"):
        validate(math.nan, num_schema, location="score")
    with pytest.raises(ValueError, match="Invalid score number"):
        validate(math.inf, num_schema, location="score")


def test_contracts_toolspec_canonical_and_effects() -> None:
    """ToolSpec normalizes aliases and classifies observation vs mutation correctly."""
    spec_tm = ToolSpec(
        name="task_manager",
        description="Task manager",
        schema={"type": "object"},
        builtin=True,
    )
    # Default action
    assert spec_tm.canonical({}) == {"action": "list"}
    # Alias normalization & parameter renames
    canon_tm = spec_tm.canonical({"action": "run_background", "working_dir": "/tmp"})
    assert canon_tm["action"] == "run"
    assert canon_tm["cwd"] == "/tmp"
    assert "working_dir" not in canon_tm
    # Effects
    assert spec_tm.effect_for({"action": "list"}) == "observation"
    assert spec_tm.effect_for({"action": "run"}) == "mutation"

    spec_wt = ToolSpec(
        name="worktree",
        description="Worktree manager",
        schema={"type": "object"},
        builtin=True,
    )
    canon_wt = spec_wt.canonical({"action": "add", "name": "feat-x"})
    assert canon_wt["action"] == "create"
    assert canon_wt["branch"] == "feat-x"
    assert "name" not in canon_wt
    assert spec_wt.effect_for({"action": "list"}) == "observation"
    assert spec_wt.effect_for({"action": "create"}) == "mutation"

    spec_diag = ToolSpec(
        name="diagnostics_check",
        description="Diagnostics",
        schema={"type": "object"},
        builtin=True,
    )
    assert spec_diag.effect_for({}) == "observation"
    assert spec_diag.effect_for({"command": "uname"}) == "mutation"

    spec_sh = ToolSpec(
        name="shell_execution",
        description="Shell execution",
        schema={"type": "object"},
        builtin=True,
    )
    assert spec_sh.effect_for({"command": "ls -la"}) == "observation"
    assert spec_sh.effect_for({"command": "echo test"}) == "observation"
    assert spec_sh.effect_for({"command": "rm -rf /"}) == "mutation"
    assert spec_sh.effect_for({"command": 'unclosed " quote'}) == "unknown"

    # Schema export formats
    native = spec_sh.native()
    assert native["type"] == "function"
    assert native["function"]["name"] == "shell_execution"
    mcp_fmt = spec_sh.mcp()
    assert mcp_fmt["name"] == "shell_execution"
    assert "inputSchema" in mcp_fmt


# ============================================================================
# 2. Bounded execution results contracts (agentcli.bounds)
# ============================================================================


def test_bounds_bound_result_stream_truncation_and_tight_envelope() -> None:
    """bound_result truncates stdout/stderr gracefully and fails cleanly when budget is insufficient."""
    import json
    from typing import NamedTuple

    class StreamCase(NamedTuple):
        task_id: str
        agent_type: SubAgentType
        success: bool
        returncode: int
        cwd: str
        stdout: str
        stderr: str
        limit: int

    cases: list[StreamCase] = [
        StreamCase(
            task_id="stream-ascii",
            agent_type=SubAgentType.SHELL_EXECUTION,
            success=True,
            returncode=0,
            cwd="/some/ascii/path",
            stdout="STANDARD_OUTPUT_" * 50,
            stderr="STANDARD_ERROR_" * 50,
            limit=300,
        ),
        StreamCase(
            task_id="stream-utf8-escaped",
            agent_type=SubAgentType.SHELL_EXECUTION,
            success=False,
            returncode=127,
            cwd='/path/with/"quotes"/and/\\backslashes\\',
            stdout='Output with "escaped quotes", \\backslashes\\, \t tabs, and \n newlines. 🚀 🌟 '
            * 30,
            stderr='Error trace with "quoted message" and UTF-8 glyphs: 💥 ⚠️ \t\n ' * 30,
            limit=350,
        ),
    ]

    for c in cases:
        orig_stdout: str = c.stdout
        orig_stderr: str = c.stderr
        res = SubAgentResult(
            task_id=c.task_id,
            agent_type=c.agent_type,
            success=c.success,
            output={
                "stdout": orig_stdout,
                "stderr": orig_stderr,
                "returncode": c.returncode,
                "cwd": c.cwd,
            },
        )
        bounded = bound_result(res, limit=c.limit)

        # Original result identity and success state must be strictly preserved
        assert bounded.task_id == c.task_id
        assert bounded.agent_type == c.agent_type
        assert bounded.success is c.success
        assert bounded.output is not None

        out_dict = bounded.output
        assert out_dict["truncated"] is True
        assert out_dict["returncode"] == c.returncode
        assert out_dict["cwd"] == c.cwd
        assert isinstance(out_dict["stdout"], str)
        assert isinstance(out_dict["stderr"], str)

        # Content must be truncated prefix of original and actually reduced
        assert orig_stdout.startswith(out_dict["stdout"])
        assert orig_stderr.startswith(out_dict["stderr"])
        assert len(out_dict["stdout"]) < len(orig_stdout) or len(out_dict["stderr"]) < len(
            orig_stderr
        )

        # Serialized UTF-8 length must not exceed limit
        ser = json.dumps(out_dict, ensure_ascii=False).encode("utf-8")
        assert len(ser) <= c.limit

    # Independent fresh insufficient-envelope case: fails closed cleanly
    res_tight = SubAgentResult(
        task_id="stream-tight",
        agent_type=SubAgentType.SHELL_EXECUTION,
        success=True,
        output={
            "stdout": "oversized stdout content " * 10,
            "stderr": "",
            "returncode": 0,
            "extra_large_metadata": "X" * 100,
        },
    )
    bounded_tight = bound_result(res_tight, limit=30)
    assert bounded_tight.task_id == "stream-tight"
    assert bounded_tight.agent_type == SubAgentType.SHELL_EXECUTION
    assert bounded_tight.success is False
    assert bounded_tight.error == "Output limit cannot represent required envelope"
    assert bounded_tight.output is None


def test_bounds_bound_result_structured_dictionary_truncation() -> None:
    """bound_result preserves control task metadata while truncating content."""
    import json
    from typing import Any

    expected_controls: dict[str, Any] = {
        "id": "struct-task-99",
        "task_id": "struct-task-99",
        "status": "completed",
        "returncode": 0,
        "total": 2,
    }
    child_one: dict[str, Any] = {
        "id": "child-1",
        "task_id": "child-1",
        "status": "done",
        "returncode": 0,
    }
    child_two: dict[str, Any] = {
        "id": "child-2",
        "task_id": "child-2",
        "status": "running",
        "returncode": None,
    }
    expected_children: list[dict[str, Any]] = [child_one, child_two]

    res = SubAgentResult(
        task_id="t-structured",
        agent_type=SubAgentType.TASK_MANAGER,
        success=True,
        output={
            "id": expected_controls["id"],
            "task_id": expected_controls["task_id"],
            "status": expected_controls["status"],
            "returncode": expected_controls["returncode"],
            "content": 'Long structured payload with UTF-8: 🌲 🔍 and "escaped quotes" \t\n ' * 100,
            "tasks": [
                {
                    "id": child_one["id"],
                    "task_id": child_one["task_id"],
                    "status": child_one["status"],
                    "returncode": child_one["returncode"],
                    "heavy_debug_logs": "trace " * 50,
                },
                {
                    "id": child_two["id"],
                    "task_id": child_two["task_id"],
                    "status": child_two["status"],
                    "returncode": child_two["returncode"],
                    "heavy_debug_logs": "trace " * 50,
                },
            ],
            "total": expected_controls["total"],
        },
    )

    budget = 400
    bounded = bound_result(res, limit=budget)

    # SubAgentResult identity must be preserved
    assert bounded.task_id == "t-structured"
    assert bounded.agent_type == SubAgentType.TASK_MANAGER
    assert bounded.success is True
    assert bounded.output is not None

    out = bounded.output
    # Exact serialized byte size <= budget
    ser_bytes = json.dumps(out, default=str).encode("utf-8")
    assert len(ser_bytes) <= budget

    # Top-level control fields preserved
    assert out["id"] == expected_controls["id"]
    assert out["task_id"] == expected_controls["task_id"]
    assert out["status"] == expected_controls["status"]
    assert out["returncode"] == expected_controls["returncode"]
    assert out["total"] == expected_controls["total"]
    assert out["truncated"] is True
    assert out["evidence_available"] is False

    # Child control dictionaries preserved exactly
    tasks = out["tasks"]
    assert isinstance(tasks, list)
    assert len(tasks) == 2
    for idx, expected_child in enumerate(expected_children):
        task_row = tasks[idx]
        assert isinstance(task_row, dict)
        for k, v in expected_child.items():
            assert task_row[k] == v
        # Heavy debug details stripped
        assert "heavy_debug_logs" not in task_row

    # Fresh, independent oversized input for tight-envelope rejection
    res_fresh_tight = SubAgentResult(
        task_id="t-structured-tight",
        agent_type=SubAgentType.TASK_MANAGER,
        success=True,
        output={
            "id": "struct-tight",
            "task_id": "struct-tight",
            "status": "completed",
            "returncode": 0,
            "tasks": [{"id": f"child-{i}", "status": "running"} for i in range(10)],
            "content": "massive content " * 100,
        },
    )
    bounded_tight = bound_result(res_fresh_tight, limit=30)
    assert bounded_tight.task_id == "t-structured-tight"
    assert bounded_tight.agent_type == SubAgentType.TASK_MANAGER
    assert bounded_tight.success is False
    assert bounded_tight.error == "Output limit cannot represent required envelope"
    assert bounded_tight.output is None


def test_bounds_read_text_and_walk_entries_in_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-worker read_text and walk_entries enforce limits and handle symlinks/junctions."""
    monkeypatch.setattr(io_boundary, "in_worker", True)

    policy = RunPolicy(root=tmp_path, max_file_bytes=10)
    test_file = tmp_path / "test.txt"
    test_file.write_text("12345", encoding="utf-8")

    # Valid read within limit
    assert read_text(test_file, policy) == "12345"

    # Exceeding file limit raises ValueError
    overflow_file = tmp_path / "overflow.txt"
    overflow_file.write_text("123456789012345", encoding="utf-8")
    with pytest.raises(ValueError, match="File exceeds acquisition limit"):
        read_text(overflow_file, policy)

    # walk_entries entry limit and depth enforcement
    d1 = tmp_path / "d1"
    d1.mkdir()
    (d1 / "f1.txt").write_text("content", encoding="utf-8")
    d2 = d1 / "d2"
    d2.mkdir()
    (d2 / "f2.txt").write_text("content", encoding="utf-8")

    entries = list(walk_entries(tmp_path, policy, max_depth=1))
    # Depth 1 should see d1, but not dive into d2
    entry_names = [p.name for p, _, _ in entries]
    assert "d1" in entry_names
    assert "d2" not in entry_names

    # Exceeding max_entries raises ValueError
    with pytest.raises(ValueError, match="Filesystem traversal entry limit exceeded; narrow scope"):
        list(walk_entries(tmp_path, policy, max_entries=1))

    # Invalid max_depth bounds
    with pytest.raises(ValueError, match="Traversal depth must be between zero and 20"):
        list(walk_entries(tmp_path, policy, max_depth=25))


def test_bounds_scan_mtimes_in_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """scan_mtimes requires in_worker, limits root count, and collects file mtimes."""
    # Without in_worker raises RuntimeError
    monkeypatch.setattr(io_boundary, "in_worker", False)
    policy = RunPolicy(root=tmp_path)
    with pytest.raises(RuntimeError, match="Native watcher scans require the owned helper"):
        scan_mtimes([tmp_path], policy, ignored=set(), extensions=set())

    # Inside worker
    monkeypatch.setattr(io_boundary, "in_worker", True)
    f1 = tmp_path / "a.py"
    f1.write_text("print(1)", encoding="utf-8")
    f2 = tmp_path / "b.txt"
    f2.write_text("text", encoding="utf-8")

    # Extension filtering
    mtimes = scan_mtimes([tmp_path], policy, ignored=set(), extensions={".py"})
    assert any(p.name == "a.py" for p in mtimes)
    assert not any(p.name == "b.txt" for p in mtimes)

    # Over 32 root paths raises ValueError
    roots = [tmp_path / f"dir_{i}" for i in range(33)]
    with pytest.raises(ValueError, match="Watcher root count exceeds limit"):
        scan_mtimes(roots, policy, ignored=set(), extensions=set())


# ============================================================================
# 3. Worker-body filesystem and execution contracts (agentcli._io_worker)
# ============================================================================


def test_io_worker_resolve_validation() -> None:
    """_io_worker.execute validates hostname and port bounds."""
    # Invalid hostname
    with pytest.raises(ValueError, match="Invalid resolver hostname"):
        _io_worker.execute({"operation": "resolve", "host": "", "port": 80})

    with pytest.raises(ValueError, match="Invalid resolver hostname"):
        _io_worker.execute({"operation": "resolve", "host": "x" * 254, "port": 80})

    # Invalid port: boolean as int, non-positive, or > 65535
    with pytest.raises(ValueError, match="Invalid resolver port"):
        _io_worker.execute({"operation": "resolve", "host": "localhost", "port": True})

    with pytest.raises(ValueError, match="Invalid resolver port"):
        _io_worker.execute({"operation": "resolve", "host": "localhost", "port": 0})

    with pytest.raises(ValueError, match="Invalid resolver port"):
        _io_worker.execute({"operation": "resolve", "host": "localhost", "port": 70000})

    # Mocked socket.getaddrinfo returns valid rows
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
    ):
        addrs = _io_worker.execute({"operation": "resolve", "host": "example.com", "port": 80})
        assert addrs == ["93.184.216.34"]


def test_io_worker_unsupported_operation_and_walk_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_io_worker.filesystem rejects unsupported operations and captures walk errors cleanly."""
    monkeypatch.setattr(io_boundary, "in_worker", True)
    policy = RunPolicy(root=tmp_path)

    # Unsupported operation
    req = io_boundary.filesystem_request("non_existent_op", policy)
    with pytest.raises(
        NotImplementedError, match="Helper operation 'non_existent_op' is unsupported"
    ):
        _io_worker.filesystem(req)

    # Walk with narrow max_entries produces bounded error field in result
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "1.txt").write_text("a", encoding="utf-8")
    (sub / "2.txt").write_text("b", encoding="utf-8")

    req_walk = io_boundary.filesystem_request(
        "walk",
        policy,
        path=str(tmp_path),
        excludes=[],
        max_entries=1,
        max_depth=5,
    )
    walk_res = _io_worker.filesystem(req_walk)
    assert walk_res["error"] is not None
    assert "Filesystem traversal entry limit exceeded" in walk_res["error"]


def test_io_worker_shell_execution_validation_and_shims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_io_worker.filesystem handles shell execution validation rejections and built-in shims."""
    monkeypatch.setattr(io_boundary, "in_worker", True)
    policy = RunPolicy(root=tmp_path, allow_execution=True)

    # Disallowed command (e.g. denylisted command)
    req_denied = io_boundary.filesystem_request(
        "shell_execution",
        policy,
        payload={"command": "powershell -Command dangerous"},
        config={"security_mode": "denylist", "denylist": ["powershell"]},
    )
    res_denied = _io_worker.filesystem(req_denied)
    assert res_denied["success"] is False
    assert "Command rejected:" in res_denied["error"]

    # Builtin shim: 'echo hello'
    req_shim = io_boundary.filesystem_request(
        "shell_execution",
        policy,
        payload={"command": "echo hello_worker"},
    )
    res_shim = _io_worker.filesystem(req_shim)
    assert res_shim["success"] is True
    assert "hello_worker" in res_shim["output"]["stdout"]

    # Command not found
    req_notfound = io_boundary.filesystem_request(
        "shell_execution",
        policy,
        payload={"command": "nonexistent_cli_tool_xyz123 --help"},
    )
    res_notfound = _io_worker.filesystem(req_notfound)
    assert res_notfound["success"] is False
    assert "Command not found:" in res_notfound["error"]


def test_io_worker_workspace_list_tree_bounds_and_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_io_worker.filesystem workspace_list_tree validates depth/entry limits and formats tree."""
    monkeypatch.setattr(io_boundary, "in_worker", True)
    policy = RunPolicy(root=tmp_path)

    # Invalid max_depth type or range
    req_bad_depth = io_boundary.filesystem_request(
        "workspace_list_tree",
        policy,
        path=str(tmp_path),
        payload={"max_depth": "invalid"},
    )
    with pytest.raises(TypeError, match="max_depth must be an integer"):
        _io_worker.filesystem(req_bad_depth)

    req_out_depth = io_boundary.filesystem_request(
        "workspace_list_tree",
        policy,
        path=str(tmp_path),
        payload={"max_depth": 25},
    )
    with pytest.raises(ValueError, match="max_depth must be between 0 and 20"):
        _io_worker.filesystem(req_out_depth)

    # Invalid max_entries type or range
    req_bad_entries = io_boundary.filesystem_request(
        "workspace_list_tree",
        policy,
        path=str(tmp_path),
        payload={"max_entries": -5},
    )
    with pytest.raises(ValueError, match="max_entries must be between 1 and 4096"):
        _io_worker.filesystem(req_bad_entries)

    # Valid tree execution
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("code", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Readme", encoding="utf-8")

    req_tree = io_boundary.filesystem_request(
        "workspace_list_tree",
        policy,
        path=str(tmp_path),
        payload={"max_depth": 2, "max_entries": 50},
    )
    tree_res = _io_worker.filesystem(req_tree)
    assert tree_res["root"] == str(tmp_path.resolve())
    assert any("📁 src" in line for line in tree_res["tree"])
    assert any("📄 README.md" in line for line in tree_res["tree"])


# ============================================================================
# 4. Policy capabilities, rejections, and environment sanitization (agentcli.policy)
# ============================================================================


def test_policy_init_limits_and_activation(tmp_path: Path) -> None:
    """RunPolicy rejects non-positive resource limits and manages nested activation tokens."""
    with pytest.raises(ValueError, match="Resource limits must be positive"):
        RunPolicy(root=tmp_path, max_output_bytes=0)

    with pytest.raises(ValueError, match="Resource limits must be positive"):
        RunPolicy(root=tmp_path, max_file_bytes=-10)

    with pytest.raises(ValueError, match="Deadline must be between zero and 300 seconds"):
        RunPolicy(root=tmp_path, timeout_seconds=0)

    with pytest.raises(ValueError, match="Deadline must be between zero and 300 seconds"):
        RunPolicy(root=tmp_path, timeout_seconds=301)

    p1 = RunPolicy(root=tmp_path, max_tasks=1)
    p2 = RunPolicy(root=tmp_path, max_tasks=2)

    from agentcli.policy import tool_policy

    # Base level (with config dict)
    assert tool_policy({"max_tasks": 2}).max_tasks == 2

    with p1.activate():
        assert tool_policy().max_tasks == 1
        with p2.activate():
            assert tool_policy().max_tasks == 2
        # Restored to p1
        assert tool_policy().max_tasks == 1


def test_policy_path_rejections_and_protected_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RunPolicy.path rejects write in read-only mode, paths outside workspace, and protected metadata."""
    monkeypatch.setattr(io_boundary, "in_worker", True)

    read_only_policy = RunPolicy(root=tmp_path, allow_write=False)
    with pytest.raises(PolicyDenied, match="Mutation denied in read-only mode"):
        read_only_policy.path("file.txt", write=True)

    # Path outside workspace
    outside = tmp_path.parent / "outside_file.txt"
    with pytest.raises(PolicyDenied, match="Path is outside the granted workspace"):
        read_only_policy.path(outside)

    # Protected metadata directories / credentials (.git, .env, .aws, .codex, .agents)
    git_dir = tmp_path / ".git" / "config"
    with pytest.raises(PolicyDenied, match="Credential/instruction metadata is not tool-readable"):
        read_only_policy.path(git_dir)

    env_file = tmp_path / ".env.production"
    with pytest.raises(PolicyDenied, match="Credential/instruction metadata is not tool-readable"):
        read_only_policy.path(env_file)


def test_policy_authorize_rejections() -> None:
    """authorize rejects untrusted argument shapes, capability grants, and unpermitted mutations."""
    policy = RunPolicy(allow_write=False, allow_execution=False)

    # Non-dict arguments
    with pytest.raises(TypeError, match="Tool arguments must be an object"):
        policy.authorize("file_ops", ["not-a-dict"])  # type: ignore[arg-type]

    # Attempt to self-grant capabilities in payload
    with pytest.raises(PolicyDenied, match="Payload cannot grant capabilities"):
        policy.authorize("file_ops", {"operation": "write", "allow_write": True})

    # Mutation attempt under read-only policy
    with pytest.raises(PolicyDenied, match="Mutation denied in read-only mode"):
        policy.authorize("file_ops", {"operation": "write", "path": "file.txt"})

    with pytest.raises(PolicyDenied, match="Mutation denied in read-only mode"):
        policy.authorize("worktree", {"action": "create", "branch": "feat"})

    # Force/discard without allow_discard
    with pytest.raises(PolicyDenied, match="Force/discard capability is not granted"):
        policy.authorize("file_ops", {"operation": "read", "path": "file.txt", "force": True})

    # Task manager required output budget
    tiny_budget_policy = RunPolicy(max_output_bytes=2048)
    with pytest.raises(PolicyDenied, match="Task control results require max_output_bytes >= 4096"):
        tiny_budget_policy.authorize("task_manager", {"action": "list"})


def test_policy_network_url_validation_and_dns_screening() -> None:
    """RunPolicy URL validation rejects non-HTTP(S), credentials, private IPs, and non-allowed hosts."""
    # Network disabled
    no_net_policy = RunPolicy(allow_network=False)
    with pytest.raises(PolicyDenied, match="Network access is disabled"):
        no_net_policy.url("https://example.com")

    net_policy = RunPolicy(allow_network=True, allowed_hosts=("allowed.com",))

    # Disallowed host
    with pytest.raises(PolicyDenied, match="Destination is not granted"):
        net_policy.url("https://forbidden.com")

    # URL with embedded credentials
    with pytest.raises(
        PolicyDenied, match=re.escape("Only credential-free HTTP(S) URLs are supported")
    ):
        net_policy.url("https://user:pass@allowed.com")

    # Non-HTTP protocol
    with pytest.raises(
        PolicyDenied, match=re.escape("Only credential-free HTTP(S) URLs are supported")
    ):
        net_policy.url("ftp://allowed.com/resource")

    # Private / loopback IP resolution rejection
    with (
        patch.object(RunPolicy, "_addresses", return_value=["127.0.0.1"]),
        pytest.raises(
            PolicyDenied, match="Private, loopback and link-local destinations are denied"
        ),
    ):
        net_policy.url("https://allowed.com")

    # Public IP resolution succeeds
    with patch.object(RunPolicy, "_addresses", return_value=["93.184.216.34"]):
        resolved_ip = net_policy.url("https://allowed.com")
        assert resolved_ip == "93.184.216.34"


def test_policy_environment_sanitization(monkeypatch: pytest.MonkeyPatch) -> None:
    """RunPolicy.environment excludes provider tokens and strips secret/injected variables."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("PYTHONPATH", "/malicious/path")
    monkeypatch.setenv("PATH", "C:\\Windows\\System32")

    policy = RunPolicy()
    env = policy.environment()

    # Sanitized whitelist only
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENROUTER_API_KEY" not in env
    assert "PYTHONPATH" not in env
    assert "PATH" in env
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] is not None
