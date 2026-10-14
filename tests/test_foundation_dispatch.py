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
    "a40c2dbe21e48c18786816abe33786aee388390993b26320703a3cb740868b77"
)
CANDIDATE_WORKER_WINDOWS_CRLF_SHA256 = (
    "ad34b2761e5c32a4a728484ac5fa2267f981bf0f84d986dfded52889a48d4151"
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


def test_process_tree_pid_validation_strict_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify ProcessTree strictly validates PID and rejects non-ints, bools, and invalid values before native calls."""
    from unittest.mock import MagicMock

    from agentcli.processes import ProcessTree

    # Trap native calls on Windows or POSIX: record and RAISE immediately before any native call.
    # It must never delegate to native code or allow invalid/mock PIDs to reach native APIs.
    native_called = False

    class NativeAccessTrapError(RuntimeError):
        pass

    if sys.platform == "win32":
        import ctypes

        def trap_windll(*args: Any, **kwargs: Any) -> Any:
            nonlocal native_called
            native_called = True
            raise NativeAccessTrapError("WinDLL called when early validation should have rejected")

        monkeypatch.setattr(ctypes, "WinDLL", trap_windll)

    # 1. Non-integer types raise TypeError without native access
    mock_pid = MagicMock()
    with pytest.raises(TypeError) as exc_mock:
        ProcessTree(mock_pid)
    assert "Process PID must be an integer, got MagicMock" in str(exc_mock.value)
    assert not native_called, "Native API must not be called for non-integer mock"

    with pytest.raises(TypeError) as exc_str:
        ProcessTree("12345")  # type: ignore[arg-type]
    assert "Process PID must be an integer, got str" in str(exc_str.value)
    assert not native_called

    with pytest.raises(TypeError) as exc_float:
        ProcessTree(12.34)  # type: ignore[arg-type]
    assert "Process PID must be an integer, got float" in str(exc_float.value)
    assert not native_called

    # 2. Bool values raise TypeError (bool is an int subclass in Python)
    with pytest.raises(TypeError) as exc_true:
        ProcessTree(True)
    assert "Process PID must be an integer, got bool" in str(exc_true.value)
    assert not native_called

    with pytest.raises(TypeError) as exc_false:
        ProcessTree(False)
    assert "Process PID must be an integer, got bool" in str(exc_false.value)
    assert not native_called

    # 3. Non-positive integers raise ValueError
    with pytest.raises(ValueError) as exc_zero:
        ProcessTree(0)
    assert "Process PID must be positive, got 0" in str(exc_zero.value)
    assert not native_called

    with pytest.raises(ValueError) as exc_neg:
        ProcessTree(-1)
    assert "Process PID must be positive, got -1" in str(exc_neg.value)
    assert not native_called

    # 4. Windows DWORD overflow raises ValueError
    if sys.platform == "win32":
        with pytest.raises(ValueError) as exc_overflow:
            ProcessTree(0x100000000)
        assert "Process PID exceeds Windows DWORD limit" in str(exc_overflow.value)
        assert not native_called

    # 5. Negative control: in-memory mutation removing the PID guard is detected by the trap without crashing
    if sys.platform == "win32":
        orig_init = ProcessTree.__init__

        def unguarded_init(self: Any, pid: int) -> None:
            # Bypass strict validation, proceed directly to native WinDLL call
            self.pid = pid
            import ctypes

            ctypes.WinDLL("kernel32", use_last_error=True)

        monkeypatch.setattr(ProcessTree, "__init__", unguarded_init)
        try:
            with pytest.raises(NativeAccessTrapError):
                ProcessTree(mock_pid)
            assert native_called, "Removed guard must be detected by the native access trap"
        finally:
            monkeypatch.setattr(ProcessTree, "__init__", orig_init)


def test_process_tree_valid_child_ownership_and_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify ProcessTree correctly manages a real running child process and cleans up resources on close."""
    import subprocess

    from agentcli.processes import ProcessTree

    # Helper to assert child process termination within a bounded deadline
    def wait_for_termination(
        proc_obj: subprocess.Popen[Any], pid: int, timeout_sec: float = 3.0
    ) -> bool:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if not check_alive(pid) or proc_obj.poll() is not None:
                return True
            time.sleep(0.05)
        return not check_alive(pid) or proc_obj.poll() is not None

    # Spawn a real, minimal child process (owned new session/process group on POSIX)
    popen_kwargs: dict[str, Any] = {}
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"], **popen_kwargs)
    try:
        assert proc.pid > 0
        child_pid = proc.pid
        tree = ProcessTree(child_pid)
        assert tree.pid == child_pid
        if sys.platform == "win32":
            assert tree.handle is not None

        # Verify close tears down tree resources and terminates child process
        tree.close()
        if sys.platform == "win32":
            assert tree.handle is None

        # Must terminate boundedly before emergency finally cleanup
        terminated = wait_for_termination(proc, child_pid, timeout_sec=3.0)
        assert terminated, f"Child process {child_pid} remained alive after tree.close()"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    # Negative control: prove that a non-terminating close() mutation (which merely
    # clears tree.handle while leaving child alive) is rejected by this test
    retained_handles: list[tuple[ProcessTree, Any]] = []
    original_close = ProcessTree.close

    def nonterminating_close(self: ProcessTree) -> None:
        retained_handles.append((self, self.handle))
        self.handle = None  # Clears handle without closing job/killing process

    mut_proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"], **popen_kwargs
    )
    try:
        monkeypatch.setattr(ProcessTree, "close", nonterminating_close)
        mut_pid = mut_proc.pid
        mut_tree = ProcessTree(mut_pid)

        mut_tree.close()
        assert mut_tree.handle is None

        # Assert that the child is still alive, and thus wait_for_termination fails
        is_terminated = wait_for_termination(mut_proc, mut_pid, timeout_sec=0.2)
        assert not is_terminated, "Non-terminating close mutation must leave child alive"
    finally:
        for t_obj, h in retained_handles:
            t_obj.handle = h
            original_close(t_obj)
        if mut_proc.poll() is None:
            mut_proc.kill()
            mut_proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_unmanaged_standalone_grep_compatibility(tmp_path: Path) -> None:
    """Verify that unmanaged standalone GrepSearchAgent executes directly via local handler (Phase 3 baseline)."""
    from agentcli.subagents.base import SubAgentTask, SubAgentType
    from agentcli.subagents.grep_search import GrepSearchAgent

    target_dir = tmp_path / "src"
    target_dir.mkdir()
    (target_dir / "app.py").write_text(
        "import logging\nlogger = logging.getLogger(__name__)\n", encoding="utf-8"
    )

    # Standalone instantiation: no active policy, no manager, default config
    agent = GrepSearchAgent(config={"working_dir": str(target_dir)})
    task = SubAgentTask(
        agent_type=SubAgentType.GREP_SEARCH,
        payload={"query": "logging", "path": str(target_dir)},
    )
    res = await agent.run(task)
    assert res.success is True
    assert res.output["engine"] in {"python", "ripgrep"}
    assert len(res.output["matches"]) >= 1


@pytest.mark.asyncio
async def test_governed_grep_fails_closed_on_missing_helper(tmp_path: Path) -> None:
    """Verify that under any explicit policy, GrepSearchAgent fails closed before local invocation."""
    from agentcli.subagents.base import SubAgentTask, SubAgentType
    from agentcli.subagents.grep_search import GrepSearchAgent

    target_dir = tmp_path / "src"
    target_dir.mkdir()
    (target_dir / "app.py").write_text("import logging\n", encoding="utf-8")

    task = SubAgentTask(
        agent_type=SubAgentType.GREP_SEARCH,
        payload={"query": "logging", "path": str(target_dir)},
    )

    # 1. Governed via active_policy context
    active_pol = RunPolicy(root=tmp_path, allow_write=False)
    agent1 = GrepSearchAgent(config={"working_dir": str(target_dir)})
    with active_pol.activate():
        res1 = await agent1.run(task)
    assert res1.success is False
    assert "filesystem_tool' is unsupported" in (res1.error or "")

    # 2. Governed via self.policy assigned after construction
    agent2 = GrepSearchAgent(config={"working_dir": str(target_dir)})
    agent2.policy = RunPolicy(root=tmp_path, allow_write=False)
    res2 = await agent2.run(task)
    assert res2.success is False
    assert "filesystem_tool' is unsupported" in (res2.error or "")

    # 3. Governed via explicit configuration policy
    agent3 = GrepSearchAgent(
        config={"working_dir": str(target_dir), "policy": RunPolicy(root=tmp_path)}
    )
    res3 = await agent3.run(task)
    assert res3.success is False
    assert "filesystem_tool' is unsupported" in (res3.error or "")

    # 4. Governed via manager policy
    class FakeManager:
        def __init__(self, pol: RunPolicy) -> None:
            self.policy = pol

    agent4 = GrepSearchAgent(config={"working_dir": str(target_dir)})
    agent4.manager = FakeManager(RunPolicy(root=tmp_path))  # type: ignore[attr-defined]
    res4 = await agent4.run(task)
    assert res4.success is False
    assert "filesystem_tool' is unsupported" in (res4.error or "")

    # 5. Governed via task_manager policy
    agent5 = GrepSearchAgent(config={"working_dir": str(target_dir)})
    agent5.task_manager = FakeManager(RunPolicy(root=tmp_path))  # type: ignore[attr-defined]
    res5 = await agent5.run(task)
    assert res5.success is False
    assert "filesystem_tool' is unsupported" in (res5.error or "")


def test_plugin_governance_dynamic_policy_and_side_effect_prevention(tmp_path: Path) -> None:
    """Verify plugin loading dynamic policy resolution, explicit denials, and import side effect prevention."""
    from agentcli.agent.registry import ToolRegistry
    from agentcli.runtime import RunContext

    # Plugin with top-level sentinel to verify whether import side effects executed
    marker_file = tmp_path / "side_effect.marker"
    plugin_script = tmp_path / "plugin_probe.py"
    plugin_script.write_text(
        f"""from pathlib import Path
Path({str(marker_file)!r}).write_text("SIDE_EFFECT_EXECUTED")
def register_tools(registry): pass
""",
        encoding="utf-8",
    )

    # 1. Default registry created, then entered into active deny context
    # Dynamic check at load time must deny and PREVENT import side effects
    default_registry = ToolRegistry()
    deny_active_pol = RunPolicy(root=tmp_path, allow_execution=False)
    with deny_active_pol.activate():
        with pytest.raises(PermissionError) as exc_info:
            default_registry.load_plugin_file(plugin_script)
        assert "trusted execution" in str(exc_info.value)
    assert not marker_file.exists(), "Denial must occur before import side effect executes"

    # 2. Explicit denying registry context without active context
    deny_context = RunContext(policy=RunPolicy(root=tmp_path, allow_execution=False))
    deny_registry = ToolRegistry(context=deny_context)
    with pytest.raises(PermissionError) as exc_info:
        deny_registry.load_plugin_file(plugin_script)
    assert "trusted execution" in str(exc_info.value)
    assert not marker_file.exists(), "Denial must occur before import side effect executes"

    # 3. Conflicting permissive active policy cannot erase explicitly denying registry context
    permissive_active_pol = RunPolicy(root=tmp_path, allow_execution=True)
    with permissive_active_pol.activate():
        with pytest.raises(PermissionError) as exc_info:
            deny_registry.load_plugin_file(plugin_script)
        assert "trusted execution" in str(exc_info.value)
    assert not marker_file.exists(), (
        "Explicit owner denial must remain binding even under permissive active policy"
    )

    # 4. Permissive context with out-of-root plugin raises PolicyDenied
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_plugin = outside_dir / "external_plugin.py"
    outside_plugin.write_text("def register_tools(r): pass\n", encoding="utf-8")

    workspace_dir = tmp_path / "granted_workspace"
    workspace_dir.mkdir()
    trusted_workspace_context = RunContext(
        policy=RunPolicy(root=workspace_dir, allow_execution=True)
    )
    trusted_registry = ToolRegistry(context=trusted_workspace_context)

    with pytest.raises(PolicyDenied) as exc_info_path:
        trusted_registry.load_plugin_file(outside_plugin)
    assert "outside the granted workspace" in str(exc_info_path.value)
    assert not marker_file.exists()


def test_plugin_active_root_b_vs_explicit_context_root_a_regression(tmp_path: Path) -> None:
    """Exact regression: active root B and explicit context root A both allow execution,

    but a plugin residing in B (outside A) must be rejected with PolicyDenied under ALL authorities,
    preventing import side effects.
    """
    from agentcli.agent.registry import ToolRegistry
    from agentcli.runtime import RunContext

    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()

    marker_file = root_b / "import_marker.txt"
    plugin_script = root_b / "plugin_in_b.py"
    plugin_script.write_text(
        f"""from pathlib import Path
Path({str(marker_file)!r}).write_text("SIDE_EFFECT_EXECUTED")
def register_tools(registry): pass
""",
        encoding="utf-8",
    )

    context = RunContext(policy=RunPolicy(root=root_a, allow_execution=True))
    registry = ToolRegistry(context=context)

    with RunPolicy(root=root_b, allow_execution=True).activate():
        with pytest.raises(PolicyDenied) as exc_info:
            registry.load_plugin_file(plugin_script)
        assert "outside the granted workspace" in str(exc_info.value)

    assert not marker_file.exists(), (
        "Import side effects must not execute when plugin is outside registry owner root"
    )


@pytest.mark.asyncio
async def test_overlapping_policy_composition_and_conflict_rejection(tmp_path: Path) -> None:
    """Verify that multiple explicit policy authorities compose all constraints safely,

    and reject conflicting scopes with PolicyDenied before the handler runs.
    """
    from agentcli.bounds import read_text
    from agentcli.subagents.base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

    root_a = tmp_path / "owner_a"
    root_b = tmp_path / "active_b"
    root_a.mkdir()
    root_b.mkdir()
    target_b = root_b / "file_b.txt"
    target_b.write_text("OUTSIDE_OWNER_SENTINEL", encoding="utf-8")

    class ProbeAgent(SubAgent):
        async def run(self, task: SubAgentTask) -> SubAgentResult:
            p = RunPolicy()
            from agentcli.policy import active_policy

            act = active_policy.get()
            if act is not None:
                p = act
            return SubAgentResult(
                task.id,
                task.agent_type,
                True,
                output={
                    "read": read_text(str(target_b), p),
                    "root": str(p.root),
                    "max_file_bytes": p.max_file_bytes,
                    "allow_network": p.allow_network,
                },
            )

    # 1. Disjoint roots: owner root A vs active root B must reject before handler
    owner_policy = RunPolicy(root=root_a, allow_network=False, max_file_bytes=8)
    active_policy_b = RunPolicy(root=root_b, allow_network=True, max_file_bytes=1000)

    agent = ProbeAgent(SubAgentType.PLANNER)
    agent.policy = owner_policy

    with active_policy_b.activate():
        res = await agent.run(SubAgentTask(agent_type=SubAgentType.PLANNER, payload={}))
    assert res.success is False
    assert "Conflicting workspace roots cannot be reconciled" in (res.error or "")

    # 2. Narrowing child root: parent root A and child root inside A compose to narrower child root
    child_dir = root_a / "subproject"
    child_dir.mkdir()
    child_file = child_dir / "child.txt"
    child_file.write_text("CHILD_CONTENT", encoding="utf-8")

    parent_policy = RunPolicy(
        root=root_a,
        allow_network=True,
        max_file_bytes=1000,
        allow_write=True,
    )
    child_policy = RunPolicy(
        root=child_dir,
        allow_network=False,
        max_file_bytes=500,
        allow_write=True,
    )

    class ChildProbeAgent(SubAgent):
        async def run(self, task: SubAgentTask) -> SubAgentResult:
            from agentcli.policy import active_policy

            act = active_policy.get()
            assert act is not None
            return SubAgentResult(
                task.id,
                task.agent_type,
                True,
                output={
                    "root": str(act.root),
                    "allow_network": act.allow_network,
                    "max_file_bytes": act.max_file_bytes,
                },
            )

    child_agent = ChildProbeAgent(SubAgentType.PLANNER)
    child_agent.policy = child_policy

    with parent_policy.activate():
        res2 = await child_agent.run(SubAgentTask(agent_type=SubAgentType.PLANNER, payload={}))
    assert res2.success is True
    # Root narrowed to child_dir, network denied by child_policy, max_file_bytes minimized to 500
    assert res2.output["root"] == str(child_dir)
    assert res2.output["allow_network"] is False
    assert res2.output["max_file_bytes"] == 500


def test_configured_policy_binding_and_explicit_limits(tmp_path: Path) -> None:
    """Verify that config['policy'] or config['run_policy'] binds actual policy object,

    tracks boundary settings, and validates invalid policy configuration.
    """
    from agentcli.subagents.base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

    class DummyAgent(SubAgent):
        async def run(self, task: SubAgentTask) -> SubAgentResult:
            return SubAgentResult(task.id, task.agent_type, True, output="ok")

    # 1. Configured RunPolicy object bound directly
    custom_policy = RunPolicy(root=tmp_path, allow_network=False, max_file_bytes=42)
    agent = DummyAgent(SubAgentType.PLANNER, config={"policy": custom_policy})
    assert agent.policy is custom_policy
    assert agent._explicit_policy is True

    # 2. Configured run_policy alias
    agent_alias = DummyAgent(SubAgentType.PLANNER, config={"run_policy": custom_policy})
    assert agent_alias.policy is custom_policy
    assert agent_alias._explicit_policy is True

    # 3. Invalid policy object raises ValueError
    with pytest.raises(ValueError, match="must be a RunPolicy instance"):
        DummyAgent(SubAgentType.PLANNER, config={"policy": "invalid_policy_string"})

    # 4. Explicit network=False marks explicit
    net_agent = DummyAgent(SubAgentType.PLANNER, config={"allow_network": False})
    assert net_agent._explicit_policy is True
    assert net_agent.policy.allow_network is False

    # 5. Explicit limits mark explicit
    limits_agent = DummyAgent(SubAgentType.PLANNER, config={"max_file_bytes": 100})
    assert limits_agent._explicit_policy is True


@pytest.mark.asyncio
async def test_captured_deny_context_preserved_outside_context(tmp_path: Path) -> None:
    """Verify that an agent constructed inside an execution-denied active context

    captures the policy at construction and preserves governance/denial even after the context exits.
    """
    from agentcli.subagents.base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

    class Probe(SubAgent):
        async def run(self, task: SubAgentTask) -> SubAgentResult:
            return SubAgentResult(task.id, task.agent_type, True, output="local handler invoked")

    with RunPolicy(allow_execution=False).activate():
        agent = Probe(SubAgentType.SHELL_EXECUTION)

    assert agent._captured_policy is not None
    assert agent._captured_policy.allow_execution is False

    # When executed outside the active context, the captured deny policy must remain binding
    # and reject commands requiring trusted execution before handler invocation
    res = await agent.run(
        SubAgentTask(
            agent_type=SubAgentType.SHELL_EXECUTION,
            payload={"command": "curl https://example.com"},
        )
    )
    assert res.success is False
    assert "Arbitrary process execution is disabled" in (res.error or "")


def test_host_intersection_disjoint_overlapping_and_unrestricted(tmp_path: Path) -> None:
    """Verify Y01: A restricted host intersection with no members must deny network access,

    and never become the unrestricted empty tuple. Test disjoint, overlapping, and unrestricted-plus-restricted.
    """
    from agentcli.subagents.base import intersect_policies

    # 1. Disjoint restrictions: ('one.invalid',) and ('two.invalid',)
    p1 = RunPolicy(root=tmp_path, allowed_hosts=("one.invalid",))
    p2 = RunPolicy(root=tmp_path, allowed_hosts=("two.invalid",))
    disjoint_merged = intersect_policies([p1, p2])
    assert disjoint_merged.allow_network is False
    assert disjoint_merged.allowed_hosts == ()
    with pytest.raises(PolicyDenied, match="Network access is disabled"):
        disjoint_merged._url_target("https://ungranted.invalid/")
    with pytest.raises(PolicyDenied, match="Network access is disabled"):
        disjoint_merged._url_target("https://one.invalid/")

    # 2. Overlapping restrictions: ('a.invalid', 'shared.invalid') and ('b.invalid', 'shared.invalid')
    p3 = RunPolicy(root=tmp_path, allowed_hosts=("a.invalid", "shared.invalid"))
    p4 = RunPolicy(root=tmp_path, allowed_hosts=("b.invalid", "shared.invalid"))
    overlap_merged = intersect_policies([p3, p4])
    assert overlap_merged.allow_network is True
    assert overlap_merged.allowed_hosts == ("shared.invalid",)
    host, port = overlap_merged._url_target("https://shared.invalid/api")
    assert host == "shared.invalid"
    assert port == 443
    with pytest.raises(PolicyDenied, match="Destination is not granted"):
        overlap_merged._url_target("https://a.invalid/")

    # 3. Unrestricted-plus-restricted: () and ('specific.invalid',)
    p_unrestricted = RunPolicy(root=tmp_path, allowed_hosts=())
    p_restricted = RunPolicy(root=tmp_path, allowed_hosts=("specific.invalid",))
    unrestricted_plus_restricted = intersect_policies([p_unrestricted, p_restricted])
    assert unrestricted_plus_restricted.allow_network is True
    assert unrestricted_plus_restricted.allowed_hosts == ("specific.invalid",)
    host, _ = unrestricted_plus_restricted._url_target("https://specific.invalid/")
    assert host == "specific.invalid"
    with pytest.raises(PolicyDenied, match="Destination is not granted"):
        unrestricted_plus_restricted._url_target("https://other.invalid/")


@pytest.mark.asyncio
async def test_explicit_configuration_inside_permissive_active_context(tmp_path: Path) -> None:
    """Verify Y02: Constructing an agent inside a permissive active context with explicit restrictions

    (e.g., allow_network=False and tighter limits) preserves the explicit configuration independently
    of ambient context, binding those restrictions during governed execution outside that context.
    """
    from agentcli.subagents.base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

    class ProbeAgent(SubAgent):
        async def run(self, task: SubAgentTask) -> SubAgentResult:
            from agentcli.policy import active_policy

            p = active_policy.get()
            assert p is not None
            return SubAgentResult(
                task.id,
                task.agent_type,
                True,
                output={
                    "allow_network": p.allow_network,
                    "max_file_bytes": p.max_file_bytes,
                    "timeout_seconds": p.timeout_seconds,
                },
            )

    active_permissive = RunPolicy(
        root=tmp_path,
        allow_network=True,
        max_file_bytes=1000000,
        timeout_seconds=60.0,
    )

    # Construct agent with tighter explicit config while active_permissive is activated
    with active_permissive.activate():
        agent = ProbeAgent(
            SubAgentType.PLANNER,
            config={
                "working_dir": str(tmp_path),
                "allow_network": False,
                "max_file_bytes": 500,
                "command_timeout": 10.0,
            },
        )

    # Directly verify configured authority was built independently of the active context
    assert agent._explicit_policy is True
    assert agent.policy.allow_network is False
    assert agent.policy.max_file_bytes == 500
    assert agent.policy.timeout_seconds == 10.0

    # Outside the active context, execute governed agent
    res = await agent.run(SubAgentTask(agent_type=SubAgentType.PLANNER))
    assert res.success is True
    assert res.output["allow_network"] is False
    assert res.output["max_file_bytes"] == 500
    assert res.output["timeout_seconds"] == 10.0


def test_relative_plugin_path_enforces_same_canonical_resource(tmp_path: Path) -> None:
    """Verify Y03: Relative plugin path resolution must enforce path containment on the same canonical

    absolute resource across all applicable authorities before any import side effect.
    """
    from agentcli.agent.registry import ToolRegistry
    from agentcli.runtime import RunContext

    root_owner = tmp_path / "owner"
    root_active = tmp_path / "active"
    root_owner.mkdir()
    root_active.mkdir()

    marker_active = root_active / "import-marker.txt"
    (root_owner / "review_plugin.py").write_text("# inert owner plugin\n", encoding="utf-8")
    (root_active / "review_plugin.py").write_text(
        f"""from pathlib import Path
Path({str(marker_active)!r}).write_text("ACTIVE_RELATIVE_IMPORTED")
""",
        encoding="utf-8",
    )

    owner_ctx = RunContext(policy=RunPolicy(root=root_owner, allow_execution=True))
    registry = ToolRegistry(context=owner_ctx)

    # 1. Disjoint roots: same relative plugin name 'review_plugin.py' under active root_active
    # Must resolve canonical path in owner context root_owner, which fails containment check under root_active
    with RunPolicy(root=root_active, allow_execution=True).activate():
        with pytest.raises(PolicyDenied) as exc_info:
            registry.load_plugin_file("review_plugin.py")
        assert "outside the granted workspace" in str(exc_info.value)

    assert not marker_active.exists(), (
        "Import side effects must never execute for relative file outside owner/active boundary"
    )

    # 2. Shared/nested-root positive control: permitted shared directory accessible by both authorities
    nested_dir = root_owner / "shared"
    nested_dir.mkdir()
    marker_nested = nested_dir / "nested-marker.txt"
    nested_plugin = nested_dir / "valid_nested_plugin.py"
    nested_plugin.write_text(
        f"""from pathlib import Path
Path({str(marker_nested)!r}).write_text("NESTED_IMPORTED")
def register_tools(registry): pass
""",
        encoding="utf-8",
    )

    # An active policy rooted at root_owner or ancestor contains the canonical nested path
    with RunPolicy(root=root_owner, allow_execution=True).activate():
        registry.load_plugin_file(str(nested_plugin))
    assert marker_nested.exists(), "Legitimate shared/nested plugin must be loaded successfully"
