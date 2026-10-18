"""Targeted regression coverage for helper request contracts and portable dispatch.

Covers:
1. Helper main entrypoint boundaries (sys.argv, stdin, JSON parsing, oversized input rejection)
2. Helper fixed operations (path, authorize, capture/fingerprint/restore roundtrip, display_diff)
3. Resolver cardinality bounds in _io_worker.execute (empty rows, over 64 rows, valid resolution)
4. Truthful unsupported operation failures (_io_worker unsupported op, FileOps in-worker _run_local)
5. Conservative ToolSpec workspace canonical aliases, effects, and schema copy-isolation
"""

from __future__ import annotations

import io
import json
import socket
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from agentcli import _io_worker, io_boundary
from agentcli.agent.checkpoints import fingerprint
from agentcli.contracts import ToolSpec, validate
from agentcli.policy import PolicyDenied, RunPolicy

# ============================================================================
# 1. Helper main request boundary (sys.argv & stdin)
# ============================================================================


def test_io_worker_main_argv_and_stdin_handling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_io_worker.main parses valid JSON requests from argv or stdin and outputs JSON responses."""
    # main/execute/filesystem sets this global; restore its prior value after this test.
    monkeypatch.setattr(io_boundary, "in_worker", True)
    policy = RunPolicy(root=tmp_path)
    test_file = tmp_path / "hello.txt"
    test_file.write_text("sample content", encoding="utf-8")

    req = io_boundary.filesystem_request("read_text", policy, path=str(test_file))
    req_json = json.dumps(req)

    # 1. Successful execution via sys.argv[1]
    out_buf = io.StringIO()
    monkeypatch.setattr(sys, "argv", ["_io_worker.py", req_json])
    monkeypatch.setattr(sys, "stdout", out_buf)

    _io_worker.main()
    res = json.loads(out_buf.getvalue())
    assert res["ok"] is True
    assert res["result"] == "sample content"

    # 2. Successful execution via sys.stdin.buffer
    in_bytes = io.BytesIO(req_json.encode("utf-8") + b"\n")
    out_buf2 = io.StringIO()
    fake_stdin = type("FakeStdin", (), {"buffer": in_bytes})()
    monkeypatch.setattr(sys, "argv", ["_io_worker.py"])
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr(sys, "stdout", out_buf2)

    _io_worker.main()
    res2 = json.loads(out_buf2.getvalue())
    assert res2["ok"] is True
    assert res2["result"] == "sample content"

    # 3. Non-object request (JSON array instead of dict)
    out_buf3 = io.StringIO()
    monkeypatch.setattr(sys, "argv", ["_io_worker.py", json.dumps(["not", "an", "object"])])
    monkeypatch.setattr(sys, "stdout", out_buf3)

    _io_worker.main()
    res3 = json.loads(out_buf3.getvalue())
    assert res3["ok"] is False
    assert res3["kind"] == "TypeError"
    assert "must be an object" in res3["error"]

    # 4. Malformed JSON
    out_buf4 = io.StringIO()
    monkeypatch.setattr(sys, "argv", ["_io_worker.py", "{invalid-json"])
    monkeypatch.setattr(sys, "stdout", out_buf4)

    _io_worker.main()
    res4 = json.loads(out_buf4.getvalue())
    assert res4["ok"] is False
    assert "JSONDecodeError" in res4["kind"] or "ValueError" in res4["kind"]

    # 5. Oversized payload (> 2MiB limit, 2_097_152 bytes)
    oversized_data = b" " * (2_097_153)
    out_buf5 = io.StringIO()
    monkeypatch.setattr(sys, "argv", ["_io_worker.py"])
    monkeypatch.setattr(sys.stdin, "buffer", io.BytesIO(oversized_data))
    monkeypatch.setattr(sys, "stdout", out_buf5)

    _io_worker.main()
    res5 = json.loads(out_buf5.getvalue())
    assert res5["ok"] is False
    assert res5["kind"] == "ValueError"
    assert "exceeds limit" in res5["error"]

    # Actual unsupported dispatch produces a long exception; main must bound its error text.
    long_operation = "unsupported_" + "x" * 3000
    unknown = io_boundary.filesystem_request(long_operation, policy)
    out_buf6 = io.StringIO()
    monkeypatch.setattr(sys, "argv", ["_io_worker.py", json.dumps(unknown)])
    monkeypatch.setattr(sys, "stdout", out_buf6)
    _io_worker.main()
    res6 = json.loads(out_buf6.getvalue())
    assert res6["ok"] is False
    assert res6["kind"] == "NotImplementedError"
    assert len(res6["error"]) == 2048
    assert res6["error"].startswith("Helper operation 'unsupported_")
    assert "result" not in res6

    # Exercise the actual fixed shell helper with only this known local Python script.
    probe = tmp_path / "owned_probe.py"
    probe.write_text(
        "import sys\nprint('helper-stdout')\n"
        "print('helper-stderr', file=sys.stderr)\nsys.exit(int(sys.argv[1]))\n",
        encoding="utf-8",
    )
    execution_policy = RunPolicy(root=tmp_path, allow_execution=True, timeout_seconds=5)
    for expected_code in (0, 7):
        shell_request = io_boundary.filesystem_request(
            "shell_execution",
            execution_policy,
            payload={
                "command": f'"{sys.executable}" "{probe}" {expected_code}',
                "timeout": 5,
            },
            config={"security_mode": "allowlist", "allowlist": [Path(sys.executable).name]},
        )
        shell_output = io.StringIO()
        monkeypatch.setattr(sys, "argv", ["_io_worker.py", json.dumps(shell_request)])
        monkeypatch.setattr(sys, "stdout", shell_output)
        _io_worker.main()
        envelope = json.loads(shell_output.getvalue())
        assert envelope["ok"] is True
        terminal = envelope["result"]
        assert terminal["success"] is (expected_code == 0)
        assert terminal["output"]["returncode"] == expected_code
        assert terminal["output"]["stdout"].splitlines() == ["helper-stdout"]
        assert terminal["output"]["stderr"].splitlines() == ["helper-stderr"]
        assert terminal["output"]["truncated"] is False
        assert terminal["error"] == (None if expected_code == 0 else terminal["output"]["stderr"])

    # Undo of a newly created file uses a real absent-file snapshot and post-state seal.
    writable = RunPolicy(root=tmp_path, allow_write=True)
    created = tmp_path / "created_after_capture.txt"
    absent = _io_worker.filesystem(
        io_boundary.filesystem_request("capture", writable, path=str(created))
    )
    assert absent["exists"] is False
    assert absent["rel_path"] == str(created)
    created.write_bytes(b"temporary content")
    snap = io_boundary.unpack_snapshot(absent)
    snap.sealed = True
    snap.expected = fingerprint(created)
    assert snap.expected is not None
    _io_worker.filesystem(
        io_boundary.filesystem_request(
            "restore", writable, snapshot=io_boundary.pack_snapshot(snap)
        )
    )
    assert not created.exists()
    assert fingerprint(created) is None

    # None bypasses optional-field checks but must still fail actual command type validation.
    with pytest.raises(TypeError, match="command must be a string"):
        policy.authorize("shell_execution", {"command": None})
    import asyncio

    with pytest.raises(TypeError, match="command must be a string"):
        asyncio.run(policy.aauthorize("shell_execution", {"command": None}))

    # Supported native tree dispatch rejects bool budgets and respects acquisition/depth caps.
    with pytest.raises(TypeError, match="max_entries must be an integer"):
        _io_worker.filesystem(
            io_boundary.filesystem_request(
                "workspace_list_tree", policy, path=str(tmp_path), payload={"max_entries": True}
            )
        )
    ignored_root = tmp_path / "ignored_tree"
    ignored_root.mkdir()
    (ignored_root / "build.egg-info").mkdir()
    ignored_tree = _io_worker.filesystem(
        io_boundary.filesystem_request(
            "workspace_list_tree",
            policy,
            path=str(ignored_root),
            payload={"max_depth": 1, "max_entries": 1},
        )
    )
    assert ignored_tree == {"root": str(ignored_root.resolve()), "tree": []}
    depth_root = tmp_path / "depth_tree"
    depth_root.mkdir()
    (depth_root / "layer").mkdir()
    (depth_root / "layer" / "deeper.txt").write_text("not acquired", encoding="utf8")
    depth_tree = _io_worker.filesystem(
        io_boundary.filesystem_request(
            "workspace_list_tree",
            policy,
            path=str(depth_root),
            payload={"max_depth": 1, "max_entries": 4},
        )
    )
    assert depth_tree == {"root": str(depth_root.resolve()), "tree": ["\U0001f4c1 layer"]}


# ============================================================================
# 2. Fixed operation dispatch (capture, fingerprint, restore, display_diff)
# ============================================================================


def test_io_worker_checkpoint_operations_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_io_worker.filesystem executes capture, fingerprint, restore, and display_diff correctly."""
    monkeypatch.setattr(io_boundary, "in_worker", True)

    policy = RunPolicy(root=tmp_path, allow_write=True)
    target = tmp_path / "data.txt"
    target.write_text("initial state\n", encoding="utf-8")

    # 1. Fingerprint operation
    req_fp = io_boundary.filesystem_request("fingerprint", policy, path=str(target))
    fp1 = _io_worker.filesystem(req_fp)
    assert isinstance(fp1, str)
    assert len(fp1) == 64

    # 2. Capture operation
    req_cap = io_boundary.filesystem_request("capture", policy, path=str(target))
    packed_snap = _io_worker.filesystem(req_cap)
    assert isinstance(packed_snap, dict)
    assert packed_snap["rel_path"] == str(target)
    assert packed_snap["exists"] is True

    # 3. Path & Authorize operations through _io_worker.filesystem
    req_p = io_boundary.filesystem_request("path", policy, path="data.txt", write=True)
    resolved_str = _io_worker.filesystem(req_p)
    assert resolved_str == str(target.resolve())

    req_auth = io_boundary.filesystem_request(
        "authorize",
        policy,
        name="file_ops",
        payload={"operation": "write", "path": str(target)},
    )
    auth_res = _io_worker.filesystem(req_auth)
    assert auth_res is None

    # 4. Modify file and display diff
    target.write_text("initial state\nmodified line\n", encoding="utf-8")
    fp2 = fingerprint(target)

    # Seal snapshot with expected post-state
    snap_obj = io_boundary.unpack_snapshot(packed_snap)
    snap_obj.sealed = True
    snap_obj.expected = fp2
    sealed_packed = io_boundary.pack_snapshot(snap_obj)

    req_diff = io_boundary.filesystem_request("display_diff", policy, snapshot=sealed_packed)
    diff_output = _io_worker.filesystem(req_diff)
    assert isinstance(diff_output, str)
    assert "data.txt" in diff_output

    # 5. Restore operation reverts file
    req_restore = io_boundary.filesystem_request("restore", policy, snapshot=sealed_packed)
    _io_worker.filesystem(req_restore)
    assert target.read_text(encoding="utf-8") == "initial state\n"
    assert fingerprint(target) == fp1

    # 6. Negative control: restore denied in read-only mode
    read_only_policy = RunPolicy(root=tmp_path, allow_write=False)
    req_ro_restore = io_boundary.filesystem_request(
        "restore", read_only_policy, snapshot=sealed_packed
    )
    with pytest.raises(PolicyDenied, match="Mutation denied in read-only mode"):
        _io_worker.filesystem(req_ro_restore)


# ============================================================================
# 3. Resolver cardinality in _io_worker.execute
# ============================================================================


def test_io_worker_resolver_cardinality_rejections() -> None:
    """_io_worker.execute rejects empty or over-64 DNS address lists."""
    # Empty getaddrinfo returns raises ValueError
    with (
        patch("socket.getaddrinfo", return_value=[]),
        pytest.raises(ValueError, match="DNS address count exceeds supported limits"),
    ):
        _io_worker.execute({"operation": "resolve", "host": "empty.example.com", "port": 80})

    # Over 64 getaddrinfo rows raises ValueError
    oversized_rows = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (f"93.184.216.{i}", 80)) for i in range(65)
    ]
    with (
        patch("socket.getaddrinfo", return_value=oversized_rows),
        pytest.raises(ValueError, match="DNS address count exceeds supported limits"),
    ):
        _io_worker.execute({"operation": "resolve", "host": "many.example.com", "port": 80})

    # Valid resolution within limits
    valid_rows = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.35", 80)),
    ]
    with patch("socket.getaddrinfo", return_value=valid_rows):
        addrs = _io_worker.execute({"operation": "resolve", "host": "example.com", "port": 80})
        assert addrs == ["93.184.216.34", "93.184.216.35"]


# ============================================================================
# 4. Truthful unsupported operation failures
# ============================================================================


def test_io_worker_truthful_unsupported_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_io_worker truthfully rejects unsupported operations and unavailable FileOps local execution."""
    monkeypatch.setattr(io_boundary, "in_worker", True)

    policy = RunPolicy(root=tmp_path)

    # Completely unknown helper operation (raises NotImplementedError in filesystem dispatch)
    req_unknown = io_boundary.filesystem_request("unknown_action_xyz", policy)
    with pytest.raises(
        NotImplementedError, match="Helper operation 'unknown_action_xyz' is unsupported"
    ):
        _io_worker.filesystem(req_unknown)

    # file_ops interface check: rejects FileOpsAgent implementations lacking _run_local
    class MissingRunLocalAgent:
        def __init__(self, config: dict[str, Any] | None = None) -> None:
            self.config = config or {}

    from agentcli.subagents import file_ops as file_ops_module

    dummy_file = tmp_path / "dummy.txt"
    dummy_file.write_text("dummy content", encoding="utf-8")
    req_fileops = io_boundary.filesystem_request(
        "file_ops",
        policy,
        task_id="t-fileops-missing",
        read_only=True,
        payload={"operation": "read", "path": str(dummy_file)},
    )
    with monkeypatch.context() as m:
        m.setattr(file_ops_module, "FileOpsAgent", MissingRunLocalAgent)
        with pytest.raises(
            NotImplementedError, match="FileOps in-worker local execution is unavailable"
        ):
            _io_worker.filesystem(req_fileops)

    # file_ops positive execution: real FileOpsAgent with _run_local executes successfully
    req_positive = io_boundary.filesystem_request(
        "file_ops",
        policy,
        task_id="t-fileops-pos",
        read_only=True,
        payload={"operation": "read", "path": str(dummy_file)},
    )
    pos_res = _io_worker.filesystem(req_positive)
    assert isinstance(pos_res, dict)
    assert pos_res["success"] is True
    assert pos_res["output"]["content"] == "dummy content"
    assert pos_res["error"] is None


# ============================================================================
# 5. ToolSpec canonical aliases, effects, and schema copy-isolation
# ============================================================================


def test_contracts_toolspec_workspace_and_copy_isolation() -> None:
    """ToolSpec handles workspace git branch/worktree canonical action defaults and copy-isolates schemas."""
    spec_ws = ToolSpec(
        name="workspace",
        description="Workspace agent",
        schema={"type": "object", "properties": {"branch": {"type": "string"}}},
        builtin=True,
    )

    # Default action for workspace git_branch without explicit action is 'create'
    canon_br = spec_ws.canonical({"operation": "git_branch"})
    assert canon_br["operation"] == "git_branch"
    assert canon_br["action"] == "create"
    # Mutation when action != 'list'
    assert spec_ws.effect_for({"operation": "git_branch"}) == "mutation"

    # Action='list' is classified as observation
    assert spec_ws.effect_for({"operation": "git_branch", "action": "list"}) == "observation"

    # file_ops ToolSpec effect_for mutation vs observation (line 185)
    spec_fo = ToolSpec(
        name="file_ops",
        description="File ops",
        schema={"type": "object"},
        builtin=True,
    )
    assert spec_fo.effect_for({"operation": "write", "path": "a.txt"}) == "mutation"
    assert spec_fo.effect_for({"operation": "read", "path": "a.txt"}) == "observation"

    # Non-builtin ToolSpec preserves payload and static effect
    spec_custom = ToolSpec(
        name="custom_tool",
        description="Custom",
        schema={"type": "object"},
        effect="mutation",
        builtin=False,
    )
    assert spec_custom.canonical({"action": "run_background"}) == {"action": "run_background"}
    assert spec_custom.effect_for({"action": "list"}) == "mutation"

    # Schema copy-isolation in native() and mcp()
    native = spec_ws.native()
    native["function"]["parameters"]["properties"]["extra"] = {"type": "integer"}
    # Original schema is unmodified
    assert "extra" not in spec_ws.schema["properties"]

    mcp_out = spec_ws.mcp()
    mcp_out["inputSchema"]["properties"]["mcp_extra"] = {"type": "string"}
    assert "mcp_extra" not in spec_ws.schema["properties"]

    # Enum validation in schema validation contract
    enum_schema = {"type": "string", "enum": ["apple", "banana"]}
    validate("apple", enum_schema)
    with pytest.raises(ValueError, match="Invalid arguments"):
        validate("orange", enum_schema)
