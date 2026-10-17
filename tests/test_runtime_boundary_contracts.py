"""Targeted regression coverage for helper and policy boundary contracts.

Covers:
1. Parent-facing filesystem wrappers (bounds.read_text & bounds.walk_entries via owned helper)
2. Cooperative traversal boundaries (exclusions, protected entries, file-root, deadlines)
3. Watcher boundary handling (missing root, ignored dot-prefix descendants, stat OSError handling)
4. Resolver transport and address screening (url, aurl, HTTPS 443/explicit port, malformed/private IPs)
5. Authorization boundary shapes (type checks, outside-scope files, read-only branch/worktree, shell file args)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from agentcli import io_boundary
from agentcli.bounds import read_text, scan_mtimes, walk_entries
from agentcli.policy import PolicyDenied, RunPolicy

# ============================================================================
# 1. Parent-facing filesystem wrappers (real owned helper via io_boundary)
# ============================================================================


def test_bounds_parent_facing_read_text_and_walk_entries(tmp_path: Path) -> None:
    """Parent callers (in_worker=False) interact with real files through the owned helper process."""
    # Invariant: parent caller delegates to query_sync -> subprocess helper -> returns results/propagates errors
    assert io_boundary.in_worker is False

    test_file = tmp_path / "hello.txt"
    test_file.write_text("parent wrapper content", encoding="utf-8")

    sub_dir = tmp_path / "sub"
    sub_dir.mkdir()
    child_file = sub_dir / "child.txt"
    child_file.write_text("child content", encoding="utf-8")

    policy = RunPolicy(root=tmp_path)

    # 1. read_text through real subprocess helper
    content = read_text(test_file, policy)
    assert content == "parent wrapper content"

    # 2. walk_entries on directory through real subprocess helper
    results = list(walk_entries(tmp_path, policy, max_depth=5, max_entries=100))
    expected = {
        (test_file.resolve(), 1, False),
        (sub_dir.resolve(), 1, True),
        (child_file.resolve(), 2, False),
    }
    actual = {(p.resolve(), depth, is_dir) for p, depth, is_dir in results}
    assert actual == expected
    assert len(results) == len(expected)

    # Exact tuple shape (Path, int depth, bool directory)
    for p, depth, is_dir in results:
        assert isinstance(p, Path)
        assert isinstance(depth, int)
        assert isinstance(is_dir, bool)

    # 3. walk_entries on single file root
    file_results = list(walk_entries(test_file, policy))
    assert len(file_results) == 1
    p_single, depth_single, is_dir_single = file_results[0]
    assert p_single.resolve() == test_file.resolve()
    assert depth_single == 0
    assert is_dir_single is False

    # 4. Entry limit error propagated after yielding partial rows
    limited = walk_entries(tmp_path, policy, max_entries=1)
    first_path, first_depth, first_directory = next(limited)
    assert (first_path.resolve(), first_depth, first_directory) in expected
    with pytest.raises(ValueError, match="Filesystem traversal entry limit exceeded; narrow scope"):
        next(limited)


# ============================================================================
# 2. Cooperative traversal boundaries
# ============================================================================


def test_bounds_traversal_exclusions_and_protected_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """walk_entries in worker filters user excludes and skips credential/metadata paths."""
    monkeypatch.setattr(io_boundary, "in_worker", True)

    policy = RunPolicy(root=tmp_path)

    # Setup normal files, excluded folder, and protected .git folder
    normal = tmp_path / "normal.txt"
    normal.write_text("normal", encoding="utf-8")

    ignored_dir = tmp_path / "ignored_folder"
    ignored_dir.mkdir()
    (ignored_dir / "secret.txt").write_text("secret", encoding="utf-8")

    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("git config", encoding="utf-8")

    # Invariant: entries in excludes are skipped; protected paths cause policy rejection caught internally
    traversed = list(walk_entries(tmp_path, policy, excludes=frozenset(["ignored_folder"])))
    names = [p.name for p, _, _ in traversed]

    assert "normal.txt" in names
    assert "ignored_folder" not in names
    assert "secret.txt" not in names
    # .git directory itself or its children should not be yielded
    assert ".git" not in names
    assert "config" not in names


def test_bounds_traversal_cooperative_deadlines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """walk_entries aborts with TimeoutError when deadline is exceeded before or during iteration."""
    monkeypatch.setattr(io_boundary, "in_worker", True)

    policy = RunPolicy(root=tmp_path, timeout_seconds=10.0)

    # 1. Deadline exceeded while popping from pending queue in while loop
    sub_dir = tmp_path / "sub_queued"
    sub_dir.mkdir()
    (sub_dir / "child.txt").write_text("child", encoding="utf-8")

    current_time = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: current_time)

    iterator = walk_entries(tmp_path, policy)
    # First item yielded is sub_dir
    first_item = next(iterator)
    assert first_item[0].name == "sub_queued"
    assert first_item[2] is True

    # Advance clock past deadline before next pending pop (line 184)
    current_time = 1025.0
    with pytest.raises(TimeoutError, match="Filesystem traversal deadline exceeded"):
        next(iterator)

    # 2. Deadline exceeded inside directory scan (line 196 during os.scandir iteration)
    dir_scan = tmp_path / "dir_scan"
    dir_scan.mkdir()
    (dir_scan / "f1.txt").write_text("1", encoding="utf-8")
    (dir_scan / "f2.txt").write_text("2", encoding="utf-8")

    # Monotonic progression:
    # call 1: deadline init (1000.0) -> deadline is 1010.0
    # call 2: while pending check (1000.1 <= 1010.0)
    # call 3: inside scandir for f1 (1020.0 > 1010.0) -> raises TimeoutError on line 197
    clock_sequence = [1000.0, 1000.1, 1020.0]

    def mock_monotonic() -> float:
        return clock_sequence.pop(0) if clock_sequence else 1020.0

    monkeypatch.setattr(time, "monotonic", mock_monotonic)
    with pytest.raises(TimeoutError, match="Filesystem traversal deadline exceeded"):
        list(walk_entries(dir_scan, policy))


# ============================================================================
# 3. Watcher boundary handling
# ============================================================================


def test_bounds_scan_mtimes_boundary_handling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """scan_mtimes ignores missing roots, dot-prefixed descendants, and recovers from stat errors."""
    monkeypatch.setattr(io_boundary, "in_worker", True)

    policy = RunPolicy(root=tmp_path)

    # Missing root is cleanly skipped
    missing_root = tmp_path / "does_not_exist"
    assert scan_mtimes([missing_root], policy, ignored=set(), extensions=set()) == {}

    # Dot-prefix ignored descendants
    sub = tmp_path / "sub"
    sub.mkdir()
    dot_file = sub / ".cache_payload.py"
    dot_file.write_text("cache", encoding="utf-8")

    normal_file = sub / "valid.py"
    normal_file.write_text("print(1)", encoding="utf-8")

    problem_file = sub / "broken.py"
    problem_file.write_text("broken", encoding="utf-8")

    # Hook stat on problem_file only to raise OSError
    orig_stat = Path.stat

    def selective_stat(self: Path, **kwargs: Any) -> Any:
        if self == problem_file:
            raise OSError("Simulated disk error")
        return orig_stat(self, **kwargs)

    monkeypatch.setattr(Path, "stat", selective_stat)

    # A matching extension and non-exact ignore ensure the prefix filter is responsible.
    unfiltered = scan_mtimes([tmp_path], policy, ignored=set(), extensions={".py"})
    assert dot_file in unfiltered
    mtimes = scan_mtimes([tmp_path], policy, ignored={".cache"}, extensions={".py"})

    assert dot_file not in mtimes
    # broken.py raised OSError and was cleanly skipped
    assert not any(p.name == "broken.py" for p in mtimes)
    # valid.py was captured with its float mtime
    valid_paths = [p for p in mtimes if p.name == "valid.py"]
    assert len(valid_paths) == 1
    assert isinstance(mtimes[valid_paths[0]], float)
    assert mtimes[valid_paths[0]] == orig_stat(normal_file).st_mtime


# ============================================================================
# 4. Resolver transport and screening
# ============================================================================


def test_policy_resolver_sync_and_async_transport() -> None:
    """RunPolicy.url and aurl validate target endpoints, query helper transport, and screen addresses."""
    policy = RunPolicy(allow_network=True, allowed_hosts=("api.service.com",))

    # Unit test seam: mock io_boundary.query_sync and query
    sync_queries: list[dict[str, Any]] = []

    def mock_query_sync(req: dict[str, Any], pol: RunPolicy) -> list[str]:
        sync_queries.append(dict(req))
        assert pol is policy
        return ["93.184.216.34"]

    async_queries: list[dict[str, Any]] = []

    async def mock_query(req: dict[str, Any], pol: RunPolicy) -> list[str]:
        async_queries.append(dict(req))
        assert pol is policy
        return ["93.184.216.35"]

    with patch("agentcli.io_boundary.query_sync", side_effect=mock_query_sync):
        # Default HTTPS port 443
        addr = policy.url("https://api.service.com/v1/resource")
        assert addr == "93.184.216.34"
        assert len(sync_queries) == 1
        assert sync_queries[0] == {
            "operation": "resolve",
            "host": "api.service.com",
            "port": 443,
        }

        # Explicit HTTP port
        addr_port = policy.url("http://api.service.com:8080/path")
        assert addr_port == "93.184.216.34"
        assert sync_queries[1] == {
            "operation": "resolve",
            "host": "api.service.com",
            "port": 8080,
        }

    # Async transport aurl
    import asyncio

    with patch("agentcli.io_boundary.query", side_effect=mock_query):
        addr_async = asyncio.run(policy.aurl("https://api.service.com/v2/async"))
        assert addr_async == "93.184.216.35"
        assert len(async_queries) == 1
        assert async_queries[0] == {
            "operation": "resolve",
            "host": "api.service.com",
            "port": 443,
        }


def test_policy_public_address_rejections() -> None:
    """RunPolicy._public_address rejects empty, oversized (>64), loopback, private, and link-local IPs."""
    # Empty address list
    with pytest.raises(PolicyDenied, match="DNS address count exceeds supported limits"):
        RunPolicy._public_address([])

    # Over 64 addresses
    with pytest.raises(PolicyDenied, match="DNS address count exceeds supported limits"):
        RunPolicy._public_address(["93.184.216.34"] * 65)

    # Loopback (127.0.0.1)
    with pytest.raises(
        PolicyDenied, match="Private, loopback and link-local destinations are denied"
    ):
        RunPolicy._public_address(["127.0.0.1"])

    # RFC 1918 Private (10.0.0.1, 192.168.1.1)
    with pytest.raises(
        PolicyDenied, match="Private, loopback and link-local destinations are denied"
    ):
        RunPolicy._public_address(["192.168.1.1"])

    # Link-local (169.254.169.254)
    with pytest.raises(
        PolicyDenied, match="Private, loopback and link-local destinations are denied"
    ):
        RunPolicy._public_address(["169.254.169.254"])

    # Mixed list containing a private IP
    with pytest.raises(
        PolicyDenied, match="Private, loopback and link-local destinations are denied"
    ):
        RunPolicy._public_address(["93.184.216.34", "10.0.0.1"])


# ============================================================================
# 5. Authorization boundary shapes
# ============================================================================


def test_policy_authorize_argument_types_and_outside_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """authorize validates types of operation/path/files and denies files outside the granted root."""
    monkeypatch.setattr(io_boundary, "in_worker", True)

    policy = RunPolicy(root=tmp_path, allow_write=True)

    # Invalid operation type (not a string)
    with pytest.raises(TypeError, match="Operation/action must be a string"):
        policy.authorize("file_ops", {"operation": 123})

    # Invalid path key type
    with pytest.raises(TypeError, match="path must be a string"):
        policy.authorize("file_ops", {"operation": "read", "path": 456})

    # Invalid files structure (not a list or not list of strings)
    with pytest.raises(TypeError, match="files must be an array of strings"):
        policy.authorize("file_ops", {"operation": "read", "files": "not-a-list"})

    with pytest.raises(TypeError, match="files must be an array of strings"):
        policy.authorize("file_ops", {"operation": "read", "files": ["valid.txt", 123]})

    # Valid files inside root
    f1 = tmp_path / "f1.txt"
    f1.write_text("1", encoding="utf-8")
    policy.authorize("file_ops", {"operation": "read", "files": [str(f1)]})

    # List of files containing an outside-scope file
    outside_file = tmp_path.parent / "outside.txt"
    with pytest.raises(PolicyDenied, match="Path is outside the granted workspace"):
        policy.authorize("file_ops", {"operation": "read", "files": [str(f1), str(outside_file)]})


def test_policy_authorize_workspace_and_command_grants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """authorize checks workspace observation vs mutation and enforces execution capability."""
    monkeypatch.setattr(io_boundary, "in_worker", True)

    read_only_policy = RunPolicy(root=tmp_path, allow_write=False, allow_execution=False)

    # Workspace git_branch/git_worktree observation (action in {"list", "status", "diff"}) is allowed
    read_only_policy.authorize("workspace", {"operation": "git_branch", "action": "list"})
    read_only_policy.authorize("workspace", {"operation": "git_worktree", "action": "status"})

    # Workspace git_branch/git_worktree mutation (action="create" or default) denied under read_only
    with pytest.raises(PolicyDenied, match="Mutation denied in read-only mode"):
        read_only_policy.authorize("workspace", {"operation": "git_branch", "action": "create"})

    # Empty command denied
    with pytest.raises(ValueError, match="No command provided"):
        read_only_policy.command("")

    with pytest.raises(ValueError, match="No command provided"):
        read_only_policy.command("   ")

    # Command without allow_execution denied
    with pytest.raises(PolicyDenied, match="Arbitrary process execution is disabled"):
        read_only_policy.command("python script.py")

    # Task manager execution grants require allow_execution
    with pytest.raises(PolicyDenied, match="Arbitrary process execution is disabled"):
        read_only_policy.authorize("task_manager", {"action": "run", "command": "pytest"})

    # Diagnostics check with command requires allow_execution
    with pytest.raises(PolicyDenied, match="Arbitrary process execution is disabled"):
        read_only_policy.authorize("diagnostics_check", {"command": "system_diagnostic"})

    # Shell execution read commands check target path within workspace
    valid_file = tmp_path / "valid.txt"
    valid_file.write_text("content", encoding="utf-8")
    read_only_policy.authorize("shell_execution", {"command": f'cat "{valid_file}"'})

    outside_file = tmp_path.parent / "outside.txt"
    with pytest.raises(PolicyDenied, match="Path is outside the granted workspace"):
        read_only_policy.authorize("shell_execution", {"command": f'cat "{outside_file}"'})
