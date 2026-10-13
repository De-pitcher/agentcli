"""Permanent regression suite for foundation capability dispatch and isolation.

Verifies:
1. Strict helper operation allowlist (excluded operations reject before side effects).
2. Out-of-root and read-only policy enforcement (no storage or byte leaks).
3. Core allowed operations (path, authorize, read_text, walk, capture, fingerprint, restore, display_diff, resolve).
4. Intermediate Batch 1 boundary for file_ops (raises NotImplementedError on Batch 1).
5. Same-verifier rejection when actual-dispatch mutation attempts to re-enable unsafe routes.
6. Child process lifecycle containment and termination on owned-boundary probes.
7. Verification that parent and owned child helper imports bind to candidate _io_worker.
8. State restoration after direct helper calls preserving parent routing integrity.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from agentcli import _io_worker, io_boundary
from agentcli.agent.checkpoints import fingerprint
from agentcli.policy import PolicyDenied, RunPolicy
from agentcli.subagents import file_ops as fops_module

CANDIDATE_WORKER_CANONICAL_LF_SHA256 = (
    "abfb0c5835a5f637eea8632ba406d34a892be48392bc5023298e8ae4eb0f0a75"
)
CANDIDATE_WORKER_WINDOWS_CRLF_SHA256 = (
    "b919e104d39e33f1c673ca510781913fec357334e0993b030cf9157c2ea8bf18"
)


@pytest.fixture(autouse=True)
def restore_worker_routing_state() -> Any:
    """Preserve and restore io_boundary.in_worker flag around every test.

    Direct calls to _io_worker.filesystem() set io_boundary.in_worker = True in
    the current process. In actual production usage this flag is only set inside
    the isolated helper subprocess. This fixture guarantees that direct test execution
    in the parent process never leaks in_worker = True into subsequent tests, which
    would bypass owned helper routing.
    """
    original_state = io_boundary.in_worker
    try:
        yield
    finally:
        io_boundary.in_worker = original_state


def test_helper_routing_state_restoration_probe(tmp_path: Path) -> None:
    """Probe demonstrating that direct helper execution sets in_worker, but teardown restores it."""
    # Assert initial parent state
    assert io_boundary.in_worker is False, "Initial state must be parent mode (in_worker=False)"

    # Intercept query_sync to observe whether RunPolicy.path routes to helper subprocess
    calls: list[dict[str, Any]] = []
    original_query_sync = io_boundary.query_sync

    def tracked_query_sync(request: dict[str, Any], policy: RunPolicy) -> Any:
        calls.append(request)
        return original_query_sync(request, policy)

    io_boundary.query_sync = tracked_query_sync
    try:
        policy = RunPolicy(root=tmp_path, allow_write=True)

        # In parent mode (in_worker=False), policy.path routes to helper subprocess
        calls.clear()
        policy.path("test.txt")
        assert len(calls) == 1, "Parent mode must route through helper subprocess"

        # Direct call to _io_worker.execute executes filesystem() in the parent,
        # which sets io_boundary.in_worker = True
        req = io_boundary.filesystem_request("path", policy, path="test.txt")
        _io_worker.execute(req)
        assert io_boundary.in_worker is True, (
            "Direct filesystem() sets in_worker in current process"
        )

        # When contaminated (in_worker=True), policy.path executes locally and bypasses helper subprocess
        calls.clear()
        policy.path("test.txt")
        assert len(calls) == 0, "Contaminated parent mode bypassed helper routing"
    finally:
        io_boundary.query_sync = original_query_sync
        # Manually restore to confirm clean state before fixture teardown
        io_boundary.in_worker = False

    assert io_boundary.in_worker is False, "Parent routing state must be restored"


def check_alive(pid: int) -> bool:
    """Query process liveness natively; fail closed on query errors other than 87."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL

        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            last_err = ctypes.get_last_error()
            if last_err == 87:  # ERROR_INVALID_PARAMETER (process does not exist)
                return False
            raise ctypes.WinError(last_err)
        try:
            code = wintypes.DWORD()
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False


def test_candidate_worker_source_identity() -> None:
    """Verify that imported _io_worker matches canonical LF identity and raw platform checkout."""
    worker_path = Path(_io_worker.__file__).resolve()
    worker_bytes = worker_path.read_bytes()

    # 1. Platform-independent canonical LF identity check
    canonical_lf_bytes = worker_bytes.replace(b"\r\n", b"\n")
    canonical_lf_hash = hashlib.sha256(canonical_lf_bytes).hexdigest()
    assert canonical_lf_hash == CANDIDATE_WORKER_CANONICAL_LF_SHA256, (
        f"Worker canonical LF hash mismatch: got {canonical_lf_hash}, expected {CANDIDATE_WORKER_CANONICAL_LF_SHA256}"
    )

    # 2. Raw source check (CRLF on Windows git checkout with autocrlf=true, or LF on Linux/macOS)
    raw_hash = hashlib.sha256(worker_bytes).hexdigest()
    assert raw_hash in {
        CANDIDATE_WORKER_WINDOWS_CRLF_SHA256,
        CANDIDATE_WORKER_CANONICAL_LF_SHA256,
    }, f"Worker raw hash {raw_hash} does not match expected platform checkout bytes"


def test_windows_liveness_query_negative_control(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: error 5 fails closed, error 87 signals process absent."""
    if sys.platform != "win32":
        pytest.skip("Windows-only negative control for Win32 OpenProcess query semantics")

    import ctypes

    class StubFunction:
        def __call__(self, *args: Any) -> int:
            return 0

    class StubKernel:
        OpenProcess = StubFunction()
        GetExitCodeProcess = StubFunction()
        CloseHandle = StubFunction()

    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: StubKernel())

    monkeypatch.setattr(ctypes, "get_last_error", lambda: 87)
    assert check_alive(12345) is False

    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)
    with pytest.raises(PermissionError):
        check_alive(12345)

    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1)
    with pytest.raises(OSError):
        check_alive(12345)


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("file_context", {"path": "outside/secret.txt"}),
        (
            "worktree_tool",
            {"storage": "outside/denied_wt", "task_id": "t1", "payload": {}},
        ),
        (
            "worktree_method",
            {
                "storage": "outside/denied_wt",
                "method": "list_worktrees",
                "args": [],
                "kwargs": {},
            },
        ),
        ("skill_reload", {"loader": {"workspace_dir": "root"}}),
        (
            "skill_prepare",
            {
                "workspace_dir": "root",
                "records": [],
                "name": "test",
                "args": {},
                "context": {},
            },
        ),
        ("memory", {"path": "outside/mem.db", "method": "list_sessions", "args": []}),
        (
            "vectors",
            {"path": "outside/vec.db", "method": "get_embeddings_mask", "args": []},
        ),
        ("index_chunks", {"root": "root"}),
        (
            "filesystem_tool",
            {
                "name": "grep_search",
                "payload": {},
                "config": {},
                "working_dir": "root",
                "task_id": "t1",
            },
        ),
        ("watcher_scan", {"paths": ["root"], "ignored": [], "extensions": []}),
    ],
)
def test_excluded_operations_rejected_without_side_effects(
    tmp_path: Path, operation: str, payload: dict[str, Any]
) -> None:
    """All operations outside the verified core allowlist must reject immediately with NotImplementedError."""
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    secret = outside / "secret.txt"
    secret.write_text("SUPER_SECRET_OUTSIDE_SENTINEL", encoding="utf-8")

    granted = tmp_path / "granted"
    granted.mkdir(parents=True, exist_ok=True)

    policy = RunPolicy(root=granted, allow_write=False, allow_execution=False)
    req = io_boundary.filesystem_request(operation, policy, **payload)

    with pytest.raises(NotImplementedError) as exc_info:
        _io_worker.execute(req)

    assert f"Helper operation '{operation}' is unsupported on this foundation runtime" in str(
        exc_info.value
    )
    # Ensure no outside files or directories were created
    assert not (outside / "denied_wt").exists()
    assert not (outside / "mem.db").exists()
    assert not (outside / "vec.db").exists()


def test_batch1_file_ops_unavailable_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that file_ops rejects unsupported FileOpsAgent implementations with NotImplementedError."""
    granted = tmp_path / "granted"
    granted.mkdir(parents=True, exist_ok=True)
    f = granted / "inside.txt"
    f.write_text("inside content", encoding="utf-8")

    policy = RunPolicy(root=granted, allow_write=True, max_output_bytes=4096)
    req = io_boundary.filesystem_request(
        "file_ops",
        policy,
        read_only=False,
        task_id="t_fops",
        payload={"operation": "read", "path": "inside.txt"},
    )

    # 1. Independent minimal unsupported fixture class that does NOT inherit from the real class
    # or its methods, ensuring it never gains _run_local even when Issue #76 implements it.
    class UnsupportedFileOpsAgent:
        def __init__(self, config: dict[str, Any] | None = None) -> None:
            self.config = config or {}

    assert not hasattr(UnsupportedFileOpsAgent, "_run_local")

    # Verifier asserting precise NotImplementedError rejection and zero side effects
    def verify_unsupported_file_ops_rejection(executor_fn: Any) -> None:
        raised: str | None = None
        res: Any = None
        try:
            res = executor_fn(req)
        except (NotImplementedError, PolicyDenied, ValueError, RuntimeError, TypeError) as exc:
            raised = type(exc).__name__ + ": " + str(exc)

        assert raised is not None and raised.startswith("NotImplementedError"), (
            f"Expected NotImplementedError, got {raised}"
        )
        assert "FileOps in-worker local execution is unavailable on this runtime base" in raised
        assert res is None, f"Expected None result, got {res}"

    # Patch the class used by the actual dispatcher with the independent unsupported fixture
    monkeypatch.setattr(fops_module, "FileOpsAgent", UnsupportedFileOpsAgent)

    # Dispatcher must reject the unsupported fixture
    verify_unsupported_file_ops_rejection(_io_worker.execute)

    # 2. Same-test verifier rejection: if dispatcher is mutated in-memory to wrongly accept
    # the unsupported fixture (e.g. bypassing the interface check), the SAME verifier rejects it.
    original_execute = _io_worker.execute

    def regressed_execute(request: dict[str, Any]) -> Any:
        if request.get("operation") == "file_ops":
            return {"success": True, "output": "wrongly_accepted", "error": None}
        return original_execute(request)

    _io_worker.execute = regressed_execute
    try:
        with pytest.raises(AssertionError) as exc_info:
            verify_unsupported_file_ops_rejection(_io_worker.execute)
        assert "Expected NotImplementedError, got None" in str(exc_info.value)
    finally:
        _io_worker.execute = original_execute

    # 3. Prove that when the real class gains a valid _run_local interface (simulating future Issue #76),
    # the supported interface works, while the unsupported fixture test remains completely stable.
    from agentcli.subagents.base import SubAgentResult, SubAgentType

    class FutureSupportedFileOpsAgent:
        def __init__(self, config: dict[str, Any] | None = None) -> None:
            self.config = config or {}

        async def _run_local(self, task: Any) -> SubAgentResult:
            return SubAgentResult(
                task.id, SubAgentType.FILE_OPS, True, output={"read": "future_supported"}
            )

    # Real class with supported interface succeeds
    monkeypatch.setattr(fops_module, "FileOpsAgent", FutureSupportedFileOpsAgent)
    supported_res = _io_worker.execute(req)
    assert supported_res["success"] is True

    # But independent unsupported fixture continues to be rejected by the dispatcher
    monkeypatch.setattr(fops_module, "FileOpsAgent", UnsupportedFileOpsAgent)
    verify_unsupported_file_ops_rejection(_io_worker.execute)


def test_out_of_root_and_read_only_policy_enforcement(tmp_path: Path) -> None:
    """Verify out-of-root access and unauthorized writes fail closed."""
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    secret = outside / "secret.txt"
    secret.write_text("SUPER_SECRET_OUTSIDE_SENTINEL", encoding="utf-8")

    granted = tmp_path / "granted"
    granted.mkdir(parents=True, exist_ok=True)
    inside = granted / "inside.txt"
    inside.write_text("inside content", encoding="utf-8")

    # Read-only policy
    policy_ro = RunPolicy(root=granted, allow_write=False)

    # 1. Path resolution outside root
    with pytest.raises(PolicyDenied) as exc_info:
        _io_worker.execute(io_boundary.filesystem_request("path", policy_ro, path=str(secret)))
    assert "outside the granted workspace" in str(exc_info.value)

    # 2. Read text outside root
    with pytest.raises(PolicyDenied) as exc_info:
        _io_worker.execute(io_boundary.filesystem_request("read_text", policy_ro, path=str(secret)))
    assert "outside the granted workspace" in str(exc_info.value)

    # 3. Path write denied in read-only mode
    with pytest.raises(PolicyDenied) as exc_info:
        _io_worker.execute(
            io_boundary.filesystem_request("path", policy_ro, path="inside.txt", write=True)
        )
    assert "Mutation denied in read-only mode" in str(exc_info.value)

    # 4. Authorize capability elevation denied
    with pytest.raises(PolicyDenied) as exc_info:
        _io_worker.execute(
            io_boundary.filesystem_request(
                "authorize", policy_ro, name="test_tool", payload={"allow_write": True}
            )
        )
    assert "Payload cannot grant capabilities" in str(exc_info.value)

    # 5. Capture outside root denied
    policy_rw = RunPolicy(root=granted, allow_write=True)
    with pytest.raises(PolicyDenied) as exc_info:
        _io_worker.execute(io_boundary.filesystem_request("capture", policy_rw, path=str(secret)))
    assert "outside the granted workspace" in str(exc_info.value)


def test_core_allowed_operations_positives(tmp_path: Path) -> None:
    """Verify positive execution of all verified core operations."""
    granted = tmp_path / "granted"
    granted.mkdir(parents=True, exist_ok=True)
    inside = granted / "inside.txt"
    inside.write_text("original content", encoding="utf-8")

    policy = RunPolicy(root=granted, allow_write=True, max_output_bytes=4096)

    # 1. DNS resolve
    req_dns = {"operation": "resolve", "host": "127.0.0.1", "port": 80}
    res_dns = _io_worker.execute(req_dns)
    assert res_dns == ["127.0.0.1"]

    # 2. path inside root
    req_path = io_boundary.filesystem_request("path", policy, path="inside.txt")
    res_path = _io_worker.execute(req_path)
    assert Path(res_path) == inside

    # 3. authorize valid tool payload
    req_auth = io_boundary.filesystem_request(
        "authorize", policy, name="test_tool", payload={"arg": 1}
    )
    assert _io_worker.execute(req_auth) is None

    # 4. read_text inside root
    req_read = io_boundary.filesystem_request("read_text", policy, path="inside.txt")
    assert _io_worker.execute(req_read) == "original content"

    # 5. walk directory entries
    req_walk = io_boundary.filesystem_request(
        "walk", policy, path=str(granted), excludes=[], max_entries=10, max_depth=2
    )
    res_walk = _io_worker.execute(req_walk)
    assert res_walk["error"] is None
    assert len(res_walk["entries"]) == 1
    assert Path(res_walk["entries"][0][0]) == inside

    # 6. capture pre-state snapshot
    req_cap = io_boundary.filesystem_request("capture", policy, path="inside.txt")
    res_cap = _io_worker.execute(req_cap)
    assert res_cap["exists"] is True
    assert res_cap["data"] is not None

    # 7. fingerprint post-state SHA-256
    req_fp = io_boundary.filesystem_request("fingerprint", policy, path="inside.txt")
    res_fp = _io_worker.execute(req_fp)
    assert isinstance(res_fp, str) and len(res_fp) == 64

    # 8. display_diff
    inside.write_text("mutated content", encoding="utf-8")
    req_diff = io_boundary.filesystem_request("display_diff", policy, snapshot=res_cap)
    res_diff = _io_worker.execute(req_diff)
    assert "-original content" in res_diff and "+mutated content" in res_diff

    # 9. restore snapshot
    mutated_fp = fingerprint(inside)
    snap = io_boundary.unpack_snapshot(res_cap)
    snap.expected = mutated_fp
    snap.sealed = True
    req_restore = io_boundary.filesystem_request(
        "restore", policy, snapshot=io_boundary.pack_snapshot(snap)
    )
    assert _io_worker.execute(req_restore) is None
    assert inside.read_text(encoding="utf-8") == "original content"


def test_same_verifier_actual_dispatch_mutation_rejection(tmp_path: Path) -> None:
    """Same verifier rejection: if actual execute entrypoint is mutated to re-enable unsafe route, verifier fails."""
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    secret = outside / "secret.txt"
    secret.write_text("SUPER_SECRET_OUTSIDE_SENTINEL", encoding="utf-8")

    granted = tmp_path / "granted"
    granted.mkdir(parents=True, exist_ok=True)
    policy = RunPolicy(root=granted, allow_write=False, allow_execution=False)

    # Standard verifier check for file_context:
    def verify_file_context_rejection(executor_fn: Any) -> None:
        req = io_boundary.filesystem_request("file_context", policy, path=str(secret))
        res = None
        raised = None
        try:
            res = executor_fn(req)
        except (NotImplementedError, PolicyDenied, ValueError, RuntimeError, TypeError) as exc:
            raised = type(exc).__name__ + ": " + str(exc)

        # Assertive policy requirement
        assert raised is not None and raised.startswith("NotImplementedError"), (
            f"Expected NotImplementedError, got {raised}"
        )
        assert res is None, f"Expected None result, got {res}"

    # Verify candidate passes:
    verify_file_context_rejection(_io_worker.execute)

    # Mutate actual execute entrypoint in-memory to simulate unsafe legacy dispatch:
    original_execute = _io_worker.execute

    def regressed_execute(request: dict[str, Any]) -> Any:
        if request.get("operation") == "file_context":
            from agentcli.files import expand_file_references

            return expand_file_references("@" + request["path"], cache=None)
        return original_execute(request)

    _io_worker.execute = regressed_execute
    try:
        with pytest.raises(AssertionError) as exc_info:
            verify_file_context_rejection(_io_worker.execute)
        assert "Expected NotImplementedError, got None" in str(exc_info.value)
    finally:
        _io_worker.execute = original_execute


@pytest.mark.asyncio
async def test_owned_boundary_child_lifecycle_and_termination(tmp_path: Path) -> None:
    """Verify owned subprocess lifecycle, worker raw equality, and prompt child process termination."""
    pid_marker = tmp_path / "worker_child.pid"
    granted = tmp_path / "granted"
    granted.mkdir(parents=True, exist_ok=True)
    inside = granted / "sample.txt"
    inside.write_text("isolated content", encoding="utf-8")

    policy = RunPolicy(root=granted, allow_write=True, timeout_seconds=5.0)

    # Candidate worker path and raw bytes from checkout
    candidate_worker_path = Path(_io_worker.__file__).resolve()
    candidate_root = Path(__file__).resolve().parents[1]

    # Instrumented shim that records child PID, asserts child raw source bytes match
    # the candidate checkout, verifies canonical LF identity, and runs worker main
    shim_script = (
        "import os, sys, hashlib\n"
        "from pathlib import Path\n"
        f"Path({str(pid_marker)!r}).write_text(str(os.getpid()))\n"
        f"sys.path.insert(0, {str(candidate_root)!r})\n"
        "sys.meta_path[:] = [f for f in sys.meta_path if not getattr(f, '__module__', '').startswith('__editable__')]\n"
        "from agentcli import _io_worker\n"
        "child_worker_path = Path(_io_worker.__file__).resolve()\n"
        "child_bytes = child_worker_path.read_bytes()\n"
        f"candidate_bytes = Path({str(candidate_worker_path)!r}).read_bytes()\n"
        "assert child_bytes == candidate_bytes, 'Child worker raw bytes do not equal candidate checkout'\n"
        "canonical_lf = child_bytes.replace(b'\\r\\n', b'\\n')\n"
        "canonical_hash = hashlib.sha256(canonical_lf).hexdigest()\n"
        f"assert canonical_hash == {CANDIDATE_WORKER_CANONICAL_LF_SHA256!r}, f'Child worker LF mismatch: {{canonical_hash}}'\n"
        "_io_worker.main()\n"
    )

    original_command = io_boundary.worker_command
    io_boundary.worker_command = lambda: [sys.executable, "-I", "-c", shim_script]
    try:
        req = io_boundary.filesystem_request("read_text", policy, path="sample.txt")
        result = await io_boundary.query(req, policy)
        assert result == "isolated content"

        # Verify child PID was captured
        assert pid_marker.exists()
        child_pid = int(pid_marker.read_text())
        assert child_pid > 0

        # Verify child process terminated promptly
        # Wait up to 2 seconds for OS handle cleanup
        deadline = time.monotonic() + 2.0
        terminated = False
        while time.monotonic() < deadline:
            if not check_alive(child_pid):
                terminated = True
                break
            await asyncio.sleep(0.02)

        assert terminated, f"Child worker process {child_pid} remained alive after query completion"
    finally:
        io_boundary.worker_command = original_command
