import asyncio
import json
import sys
from typing import Any

import pytest

from agentcli.agent.registry import ToolRegistry
from agentcli.mcp import MCPServer, run_mcp


@pytest.mark.asyncio
async def test_mcp_worker_cleanup_survives_release() -> None:
    """Worker finally cleanup survives shutdown and protects the registry."""
    entered = asyncio.Event()
    release = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_completed = asyncio.Event()
    cleanup_interrupted = asyncio.Event()

    class Registry:
        def __init__(self) -> None:
            self.calls = 0

        async def aclose(self) -> None:
            self.calls += 1

    class CleanupServer(MCPServer):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)

        async def _worker_loop(self, worker_id: int) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                try:
                    await release.wait()
                    cleanup_completed.set()
                except asyncio.CancelledError:
                    cleanup_interrupted.set()
                    raise

    registry = Registry()
    server = CleanupServer(registry=registry, max_concurrency=1)
    reader = asyncio.StreamReader()
    owner = asyncio.create_task(server.run(reader))
    try:
        await asyncio.wait_for(entered.wait(), 1.0)
        first = await asyncio.wait_for(server.aclose(), 1.4)
        assert first is False
        assert registry.calls == 0
        assert server._closed is False
        assert cleanup_started.is_set()
        assert not cleanup_completed.is_set()
        assert not cleanup_interrupted.is_set()
        assert sum(not task.done() for task in server._worker_tasks) == 1

        release.set()
        await asyncio.wait_for(
            asyncio.gather(owner, *server._worker_tasks, return_exceptions=True), 2.0
        )
        assert cleanup_completed.is_set()
        assert not cleanup_interrupted.is_set()
        assert await asyncio.wait_for(server.aclose(), 1.4) is True
        assert registry.calls == 1
        assert server._closed is True
    finally:
        release.set()
        reader.feed_eof()
        await asyncio.wait_for(
            asyncio.gather(owner, *server._worker_tasks, return_exceptions=True), 2.0
        )
        await asyncio.wait_for(server.aclose(), 1.4)


@pytest.mark.asyncio
async def test_mcp_initialize():
    server = MCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    }
    resp = await server.handle_request(req)
    assert resp is not None
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"]["name"] == "agentcli"


@pytest.mark.asyncio
async def test_mcp_ping():
    server = MCPServer()
    req = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    resp = await server.handle_request(req)
    assert resp == {"jsonrpc": "2.0", "id": 2, "result": {}}


@pytest.mark.asyncio
async def test_mcp_tools_list():
    registry = ToolRegistry()
    registry.register_callable("custom_calc", lambda a, b: a + b, description="Add numbers")
    server = MCPServer(registry=registry)

    req = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    resp = await server.handle_request(req)
    assert resp is not None
    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "file_ops" in tool_names
    assert "code_analyzer" in tool_names
    assert "custom_calc" in tool_names


@pytest.mark.asyncio
async def test_mcp_tools_call():
    registry = ToolRegistry()
    registry.register_callable("multiply", lambda x, y: x * y)
    server = MCPServer(registry=registry)

    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "multiply",
            "arguments": {"x": 6, "y": 7},
        },
    }
    resp = await server.handle_request(req)
    assert resp is not None
    assert resp["id"] == 4
    assert resp["result"]["isError"] is False
    assert "42" in resp["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_mcp_unknown_method_and_notification():
    server = MCPServer()
    # Notification returns None
    assert await server.handle_request({"method": "notifications/initialized"}) is None

    # Unknown method returns JSON-RPC error
    req = {"jsonrpc": "2.0", "id": 99, "method": "invalid/method"}
    resp = await server.handle_request(req)
    assert resp is not None
    assert resp["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_mcp_empty_message():
    """Test handling of message without method."""
    server = MCPServer()
    resp = await server.handle_request({"jsonrpc": "2.0", "id": 1})
    assert resp is None


@pytest.mark.asyncio
async def test_mcp_tools_call_unknown_tool():
    """Test calling an unknown tool returns error."""
    server = MCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "nonexistent_tool", "arguments": {}},
    }
    resp = await server.handle_request(req)
    assert resp is not None
    assert resp["id"] == 5
    assert resp["result"]["isError"] is True
    assert "nonexistent_tool" in resp["result"]["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_mcp_tools_call_failing_tool():
    """Test calling a tool that fails returns isError=True."""
    registry = ToolRegistry()

    def failing_tool(x: int) -> int:
        raise ValueError("tool failed")

    registry.register_callable("failer", failing_tool)
    server = MCPServer(registry=registry)

    req = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {"name": "failer", "arguments": {"x": 1}},
    }
    resp = await server.handle_request(req)
    assert resp is not None
    assert resp["id"] == 6
    assert resp["result"]["isError"] is True
    assert "tool failed" in resp["result"]["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_mcp_run_method(monkeypatch):
    """Test the run() method processes messages from reader."""
    import asyncio
    import json

    server = MCPServer()
    reader = asyncio.StreamReader()

    # Feed initialize, ping, bad json, and empty lines
    reader.feed_data(b'{"jsonrpc": "2.0", "id": 10, "method": "ping"}\n')
    reader.feed_data(b"\n")
    reader.feed_data(b"invalid json\n")
    reader.feed_eof()

    # Capture stdout
    import sys
    from io import StringIO

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        await server.run(reader=reader)
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    lines = [json.loads(l) for l in output.strip().split("\n") if l]
    assert len(lines) == 2
    assert lines[0]["id"] == 10
    assert lines[0]["result"] == {}
    assert lines[1]["error"]["code"] == -32700


@pytest.mark.asyncio
async def test_mcp_tool_definitions_descriptions():
    """Test that tool definitions have proper descriptions."""
    server = MCPServer()
    definitions = server._tool_definitions()
    tool_map = {d["name"]: d for d in definitions}

    assert "file_ops" in tool_map
    assert "Perform file operations" in tool_map["file_ops"]["description"]
    assert "shell_execution" in tool_map
    assert "Execute sandboxed shell commands" in tool_map["shell_execution"]["description"]
    assert "code_analyzer" in tool_map
    assert (
        "Analyze code files for bugs, security issues, performance problems"
        in tool_map["code_analyzer"]["description"]
    )
    assert "web_search" in tool_map
    assert "Search the web for information" in tool_map["web_search"]["description"]


def test_run_mcp_entrypoint(monkeypatch):
    """Test the run_mcp entry point function."""

    async def mock_run(self, reader=None):
        return None

    monkeypatch.setattr(MCPServer, "run", mock_run)
    assert run_mcp() == 0


def test_run_mcp_handles_interrupt(monkeypatch):
    """Test run_mcp handles KeyboardInterrupt."""

    async def mock_run_ki(self, reader=None):
        raise KeyboardInterrupt()

    monkeypatch.setattr(MCPServer, "run", mock_run_ki)
    assert run_mcp() == 0


def test_run_mcp_handles_exception(monkeypatch):
    """Test run_mcp handles unexpected exceptions."""

    async def mock_run_exc(self, reader=None):
        raise RuntimeError("server crashed")

    monkeypatch.setattr(MCPServer, "run", mock_run_exc)
    # Should return 1 on error
    assert run_mcp() == 1


@pytest.mark.asyncio
async def test_mcp_default_file_ops_happy_and_denial(tmp_path):
    from agentcli.policy import RunPolicy
    from agentcli.runtime import RunContext

    test_file = tmp_path / "hello.txt"
    test_file.write_text("file content", encoding="utf-8")

    # Read-only default explicitly bound to tmp_path root (no allow_write)
    ctx = RunContext(policy=RunPolicy(root=tmp_path, allow_write=False))
    registry = ToolRegistry(context=ctx, tool_configs={"file_ops": {"working_dir": str(tmp_path)}})
    server = MCPServer(registry=registry)

    # Happy path: read
    req_read = {
        "jsonrpc": "2.0",
        "id": 101,
        "method": "tools/call",
        "params": {
            "name": "file_ops",
            "arguments": {"operation": "read", "path": str(test_file)},
        },
    }
    resp_read = await server.handle_request(req_read)
    assert resp_read["result"]["isError"] is False
    assert "file content" in resp_read["result"]["content"][0]["text"]

    # Denial path: write blocked in read-only default
    req_write = {
        "jsonrpc": "2.0",
        "id": 102,
        "method": "tools/call",
        "params": {
            "name": "file_ops",
            "arguments": {
                "operation": "write",
                "path": str(tmp_path / "out.txt"),
                "content": "blocked",
            },
        },
    }
    resp_write = await server.handle_request(req_write)
    assert resp_write["result"]["isError"] is True
    assert "read-only mode" in resp_write["result"]["content"][0]["text"]

    # Denial path 2: outside-root path access blocked
    outside_file = tmp_path.parent / "outside_probe.txt"
    req_outside = {
        "jsonrpc": "2.0",
        "id": 103,
        "method": "tools/call",
        "params": {
            "name": "file_ops",
            "arguments": {"operation": "read", "path": str(outside_file)},
        },
    }
    resp_outside = await server.handle_request(req_outside)
    assert resp_outside["result"]["isError"] is True
    assert "outside" in resp_outside["result"]["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_mcp_default_shell_execution_happy_and_denial(tmp_path):
    from agentcli.agent.registry import ToolRegistry
    from agentcli.policy import RunPolicy
    from agentcli.runtime import RunContext

    # 1. Default context denies arbitrary process execution
    default_server = MCPServer()
    req_default = {
        "jsonrpc": "2.0",
        "id": 200,
        "method": "tools/call",
        "params": {
            "name": "shell_execution",
            "arguments": {"command": "python -c \"print('mcp_denied_by_default')\""},
        },
    }
    resp_default = await default_server.handle_request(req_default)
    assert resp_default["result"]["isError"] is True
    assert "execution is disabled" in resp_default["result"]["content"][0]["text"].lower()

    # 2. Explicit trusted context allows safe execution
    trusted_context = RunContext(policy=RunPolicy(root=tmp_path, allow_execution=True))
    trusted_registry = ToolRegistry(context=trusted_context)
    trusted_server = MCPServer(registry=trusted_registry)

    try:
        # Happy path: safe allowed execution
        req_happy = {
            "jsonrpc": "2.0",
            "id": 201,
            "method": "tools/call",
            "params": {
                "name": "shell_execution",
                "arguments": {"command": "python -c \"print('mcp_ok')\""},
            },
        }
        resp_happy = await trusted_server.handle_request(req_happy)
        assert resp_happy["result"]["isError"] is False
        assert "mcp_ok" in resp_happy["result"]["content"][0]["text"]

        # Denial path 1: command in denylist
        req_denial = {
            "jsonrpc": "2.0",
            "id": 202,
            "method": "tools/call",
            "params": {
                "name": "shell_execution",
                "arguments": {"command": "rm -rf /"},
            },
        }
        resp_denial = await trusted_server.handle_request(req_denial)
        assert resp_denial["result"]["isError"] is True
        assert "denied" in resp_denial["result"]["content"][0]["text"].lower()

        # Denial path 2: outside-root cwd denial
        outside_dir = tmp_path.parent / "outside_cwd_probe"
        req_outside_cwd = {
            "jsonrpc": "2.0",
            "id": 203,
            "method": "tools/call",
            "params": {
                "name": "shell_execution",
                "arguments": {
                    "command": "python -c \"print('should_not_run')\"",
                    "working_dir": str(outside_dir),
                },
            },
        }
        resp_outside_cwd = await trusted_server.handle_request(req_outside_cwd)
        assert resp_outside_cwd["result"]["isError"] is True
        assert (
            "outside the granted workspace"
            in resp_outside_cwd["result"]["content"][0]["text"].lower()
        )

        # Truthful failure reporting: command with non-zero exit code
        req_nonzero = {
            "jsonrpc": "2.0",
            "id": 204,
            "method": "tools/call",
            "params": {
                "name": "shell_execution",
                "arguments": {
                    "command": "python -c \"import sys; sys.stderr.write('err_msg'); sys.exit(42)\""
                },
            },
        }
        resp_nonzero = await trusted_server.handle_request(req_nonzero)
        assert resp_nonzero["result"]["isError"] is True
        content_nonzero = resp_nonzero["result"]["content"][0]["text"]
        assert "err_msg" in content_nonzero
        assert "42" in content_nonzero
    finally:
        await trusted_context.aclose()


@pytest.mark.asyncio
async def test_mcp_default_code_analyzer_happy_and_denial(tmp_path):
    from agentcli.agent.registry import ToolRegistry
    from agentcli.policy import RunPolicy
    from agentcli.runtime import RunContext

    context = RunContext(policy=RunPolicy(root=tmp_path))
    registry = ToolRegistry(context=context)
    server = MCPServer(registry=registry)

    try:
        test_code = tmp_path / "sample.py"
        test_code.write_text("x = 1\n", encoding="utf-8")

        # Happy path: analyze existing file
        req_happy = {
            "jsonrpc": "2.0",
            "id": 301,
            "method": "tools/call",
            "params": {
                "name": "code_analyzer",
                "arguments": {"files": [str(test_code)]},
            },
        }
        resp_happy = await server.handle_request(req_happy)
        assert resp_happy["result"]["isError"] is False
        assert "files_analyzed" in resp_happy["result"]["content"][0]["text"]

        # Denial path 1: missing file within the granted root
        missing_in_root = tmp_path / "does_not_exist_in_root.py"
        req_missing = {
            "jsonrpc": "2.0",
            "id": 302,
            "method": "tools/call",
            "params": {
                "name": "code_analyzer",
                "arguments": {"files": [str(missing_in_root)]},
            },
        }
        resp_missing = await server.handle_request(req_missing)
        assert resp_missing["result"]["isError"] is True
        assert "Failed to read" in resp_missing["result"]["content"][0]["text"]
        assert "File not found" in resp_missing["result"]["content"][0]["text"]

        # Denial path 2: separate outside-root denial
        req_outside = {
            "jsonrpc": "2.0",
            "id": 303,
            "method": "tools/call",
            "params": {
                "name": "code_analyzer",
                "arguments": {"files": ["/path/outside/granted/root.py"]},
            },
        }
        resp_outside = await server.handle_request(req_outside)
        assert resp_outside["result"]["isError"] is True
        assert (
            "Path is outside the granted workspace" in resp_outside["result"]["content"][0]["text"]
        )
    finally:
        await context.aclose()


@pytest.mark.asyncio
async def test_mcp_default_web_search_happy_and_denial(monkeypatch):
    server = MCPServer()

    # Happy path with mock provider
    from agentcli.subagents.web_search import SearchResult

    provider_calls = 0

    async def mock_search(self, query, max_results=10):
        nonlocal provider_calls
        provider_calls += 1
        return [SearchResult(title="Result Title", url="https://example.com", snippet="Snippet")]

    from agentcli.subagents.web_search import DuckDuckGoProvider

    monkeypatch.setattr(DuckDuckGoProvider, "search", mock_search)

    req_happy = {
        "jsonrpc": "2.0",
        "id": 401,
        "method": "tools/call",
        "params": {
            "name": "web_search",
            "arguments": {"query": "python"},
        },
    }
    resp_happy = await server.handle_request(req_happy)
    assert resp_happy["result"]["isError"] is False
    assert "https://example.com" in resp_happy["result"]["content"][0]["text"]
    assert provider_calls == 1

    # Denial path: missing query
    req_denial = {
        "jsonrpc": "2.0",
        "id": 402,
        "method": "tools/call",
        "params": {
            "name": "web_search",
            "arguments": {},
        },
    }
    resp_denial = await server.handle_request(req_denial)
    assert resp_denial["result"]["isError"] is True
    assert "Missing required arguments fields" in resp_denial["result"]["content"][0]["text"]
    assert provider_calls == 1, "Provider invocation must not occur for malformed requests"


@pytest.mark.asyncio
async def test_mcp_server_bounded_frame_acquisition_oversized_rejection():
    """Verify that lines exceeding max_frame_bytes are rejected without unbounded memory allocation."""
    server = MCPServer(max_frame_bytes=100)
    reader = asyncio.StreamReader()

    # Oversized frame > 100 bytes
    huge_line = b'{"jsonrpc": "2.0", "id": 1, "method": "ping", "data": "' + (b"x" * 200) + b'"}\n'
    # Followed by a valid small frame
    valid_line = b'{"jsonrpc": "2.0", "id": 2, "method": "ping"}\n'

    reader.feed_data(huge_line + valid_line)
    reader.feed_eof()

    import io
    import sys

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        await server.run(reader=reader)
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    lines = [json.loads(l) for l in output.strip().split("\n") if l]
    assert len(lines) == 2
    # First line: oversized frame error rejection (-32600)
    assert lines[0]["error"]["code"] == -32600
    assert "exceeds maximum limit" in lines[0]["error"]["message"]
    # Second line: normal ping succeeded
    assert lines[1]["id"] == 2
    assert lines[1]["result"] == {}


def test_mcp_server_constructor_validation():
    """Verify strict validation of concurrency, queue size, and frame byte options."""
    # Valid options
    server = MCPServer(max_concurrency=2, max_queue_size=16, max_frame_bytes=1024)
    assert server.max_concurrency == 2
    assert server.max_queue_size == 16
    assert server.max_frame_bytes == 1024

    # Invalid non-integer or boolean coercion
    with pytest.raises(ValueError, match="max_concurrency"):
        MCPServer(max_concurrency=True)
    with pytest.raises(ValueError, match="max_concurrency"):
        MCPServer(max_concurrency=0)
    with pytest.raises(ValueError, match="max_queue_size"):
        MCPServer(max_queue_size=False)
    with pytest.raises(ValueError, match="max_frame_bytes"):
        MCPServer(max_frame_bytes=-1)

    # max_frame_bytes < 3 is invalid (minimum frame is 3 bytes e.g. {}\n)
    with pytest.raises(ValueError, match="max_frame_bytes"):
        MCPServer(max_frame_bytes=1)
    with pytest.raises(ValueError, match="max_frame_bytes"):
        MCPServer(max_frame_bytes=2)
    with pytest.raises(ValueError, match="max_frame_bytes"):
        MCPServer(max_frame_bytes=True)


@pytest.mark.asyncio
async def test_mcp_server_unresolved_close_explicit_outcome():
    """Verify that an active task that refuses or delays cancellation causes aclose to return False."""

    class MockRegistry:
        closed = False

        async def aclose(self):
            self.closed = True

    reg = MockRegistry()
    server = MCPServer(registry=reg)

    release_evt = asyncio.Event()

    async def resistant_request(msg):
        try:
            await release_evt.wait()
        except asyncio.CancelledError:
            # Simulate delayed cleanup / cancellation resistance
            await release_evt.wait()

    server._handle_request_impl = resistant_request
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"id": 1, "method": "ping"}\n')
    run_task = asyncio.create_task(server.run(reader))

    await asyncio.sleep(0.05)
    assert len(server._active_tasks) == 1

    # aclose() must return False indicating unresolved in-flight tasks and keep registry unclosed
    clean = await server.aclose()
    assert clean is False, "aclose() must return False when tasks remain unresolved"
    assert len(server._active_tasks) == 1, "Unresolved task must remain tracked"
    assert reg.closed is False, "Registry must NOT be closed while active tasks remain"

    # Now release task and complete teardown
    release_evt.set()
    reader.feed_eof()
    await asyncio.wait_for(run_task, timeout=2.0)


@pytest.mark.asyncio
async def test_mcp_server_bounded_stdio_utf8_and_next_frame():
    """Verify bounded stdio acquisition for multibyte UTF-8 and exact newline boundaries."""

    class MockStdin:
        def __init__(self, chunks: list[str]):
            self._iter = iter(chunks)
            self.read_sizes: list[int] = []

        def readline(self, size: int = -1) -> str:
            self.read_sizes.append(size)
            return next(self._iter, "")

    class MockRegistry:
        closed = False

        async def aclose(self):
            self.closed = True

    import io
    import sys

    old_stdin = sys.stdin
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        # Case 1: 20 emojis (80 UTF-8 bytes) with cap=64 -> rejected without unbounded memory
        chars1 = list("🌟" * 20 + "\n")
        mock1 = MockStdin(chars1)
        sys.stdin = mock1
        s1 = MCPServer(registry=MockRegistry(), max_frame_bytes=64)
        res1 = await s1._read_bounded_line_stdio()
        assert res1 == ""
        assert all(sz == 1 for sz in mock1.read_sizes)

        # Case 2: Exact 64-char line followed by valid ping
        chars2 = list("a" * 64 + "\n") + list('{"jsonrpc": "2.0", "id": 2, "method": "ping"}\n')
        mock2 = MockStdin(chars2)
        sys.stdin = mock2
        s2 = MCPServer(registry=MockRegistry(), max_frame_bytes=64)
        res2_1 = await s2._read_bounded_line_stdio()
        assert res2_1 == "a" * 64
        res2_2 = await s2._read_bounded_line_stdio()
        assert res2_2 is not None
        parsed = json.loads(res2_2)
        assert parsed["method"] == "ping"
        assert all(sz == 1 for sz in mock2.read_sizes)
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout


@pytest.mark.asyncio
async def test_mcp_server_saturated_queue_eof_shutdown():
    """Verify that a saturated queue still reaches clean EOF teardown when input stream closes."""

    class MockRegistry:
        closed = False

        async def aclose(self):
            self.closed = True

    reg = MockRegistry()
    server = MCPServer(registry=reg, max_queue_size=2)

    entered = asyncio.Event()

    async def blocked_handler(_msg):
        entered.set()
        await asyncio.Event().wait()

    server._handle_request_impl = blocked_handler
    reader = asyncio.StreamReader()

    # Feed 5 frames followed by EOF
    for _ in range(5):
        reader.feed_data(b'{"id": 1, "method": "ping"}\n')
    reader.feed_eof()

    import io
    import sys

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        run_task = asyncio.create_task(server.run(reader))
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        # Give bounded time for reader to finish and close to execute
        await asyncio.wait_for(run_task, timeout=1.5)
        assert run_task.done()
        assert reg.closed is True
    finally:
        sys.stdout = old_stdout


@pytest.mark.asyncio
async def test_mcp_server_serialized_idempotent_close():
    """Verify that concurrent and repeated aclose() calls are serialized and idempotent."""

    class MockRegistry:
        def __init__(self):
            self.closes = 0

        async def aclose(self):
            self.closes += 1

    reg = MockRegistry()
    server = MCPServer(registry=reg)
    reader = asyncio.StreamReader()

    run_task = asyncio.create_task(server.run(reader))
    await asyncio.sleep(0.05)

    # Call aclose concurrently 3 times
    results = await asyncio.gather(server.aclose(), server.aclose(), server.aclose())
    assert all(r is True for r in results)
    assert reg.closes == 1, "Registry must only be closed once"
    await asyncio.wait_for(run_task, timeout=2.0)
    assert run_task.done()


@pytest.mark.asyncio
async def test_mcp_server_retryable_aclose_delayed_handler():
    """R72 regression: if delayed handler delays completion, initial aclose reports False

    without permanently marking server closed. Second aclose after handler completion
    must cleanly succeed and close registry exactly once.
    """

    class MockRegistry:
        def __init__(self):
            self.closes = 0
            self.closed = False

        async def aclose(self):
            self.closes += 1
            self.closed = True

    reg = MockRegistry()
    server = MCPServer(registry=reg)

    release_gate = asyncio.Event()

    async def resistant_handler(_msg):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release_gate.wait()

    server._handle_request_impl = resistant_handler
    reader = asyncio.StreamReader()
    run_task = asyncio.create_task(server.run(reader))

    reader.feed_data(b'{"id": 1, "method": "ping"}\n')
    await asyncio.sleep(0.05)

    # First aclose: handler refuses immediate cancellation -> False, registry untouched
    clean1 = await server.aclose()
    assert clean1 is False
    assert reg.closes == 0
    assert len(server._active_tasks) == 1

    # Release delayed handler
    release_gate.set()
    await asyncio.sleep(0.05)
    assert len(server._active_tasks) == 0

    # Second aclose: clean cleanup, closes registry exactly once
    clean2 = await server.aclose()
    assert clean2 is True
    assert reg.closes == 1

    # Third aclose: idempotent
    clean3 = await server.aclose()
    assert clean3 is True
    assert reg.closes == 1
    await asyncio.wait_for(run_task, timeout=1.0)


@pytest.mark.asyncio
async def test_mcp_queue_saturation_preserves_request_id():
    """R73 regression: saturated request queue returns overload error with original request ID,

    and does not emit responses for notifications.
    """
    import io

    server = MCPServer(max_queue_size=1, max_concurrency=1)

    hang = asyncio.Event()

    async def hanging_handler(_msg):
        await hang.wait()

    server._handle_request_impl = hanging_handler
    reader = asyncio.StreamReader()

    old_stdout = sys.stdout
    captured = io.StringIO()
    sys.stdout = captured
    try:
        run_task = asyncio.create_task(server.run(reader))
        # First request enters queue and is picked up by worker (hanging)
        reader.feed_data(b'{"jsonrpc": "2.0", "id": 10, "method": "ping"}\n')
        await asyncio.sleep(0.05)

        # Second request fills the 1-slot queue
        reader.feed_data(b'{"jsonrpc": "2.0", "id": 20, "method": "ping"}\n')
        await asyncio.sleep(0.05)

        # Third request overflows queue -> should get overload response with id=30
        reader.feed_data(b'{"jsonrpc": "2.0", "id": 30, "method": "ping"}\n')
        # Fourth is a notification (no id) -> should be discarded without error response
        reader.feed_data(b'{"jsonrpc": "2.0", "method": "notify_something"}\n')
        await asyncio.sleep(0.05)

        lines = [
            json.loads(line) for line in captured.getvalue().strip().split("\n") if line.strip()
        ]
        overload_lines = [m for m in lines if m.get("error", {}).get("code") == -32000]
        assert len(overload_lines) == 1
        assert overload_lines[0]["id"] == 30
        assert overload_lines[0]["error"]["message"] == "Queue saturated"

        hang.set()
        await server.aclose()
        await asyncio.wait_for(run_task, timeout=1.0)
    finally:
        sys.stdout = old_stdout


@pytest.mark.asyncio
async def test_mcp_stdio_byte_bounded_multibyte_reading(monkeypatch):
    """R71 regression: stdio reads 1 character at a time, strictly limiting UTF-8 byte acquisition

    to <= 4 bytes per chunk (and <= cap + 1), rejecting oversize frames without consuming
    into the next frame.
    """
    import io

    server = MCPServer(max_frame_bytes=32)

    # 4-byte UTF-8 character (repeat 10 times = 40 bytes > 32 cap)
    long_multibyte = "🚀" * 10 + "\n"
    next_frame = '{"id": 42, "method": "ping"}\n'
    fake_stdin = io.StringIO(long_multibyte + next_frame)
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    old_stdout = sys.stdout
    captured = io.StringIO()
    sys.stdout = captured
    try:
        # First read: oversized frame -> returns empty string, outputs jsonrpc error
        line1 = await server._read_bounded_line_stdio()
        assert line1 == ""
        errors = [json.loads(l) for l in captured.getvalue().strip().split("\n") if l.strip()]
        assert len(errors) == 1
        assert errors[0]["error"]["code"] == -32600

        # Second read: exactly aligned next frame
        line2 = await server._read_bounded_line_stdio()
        assert line2 == '{"id": 42, "method": "ping"}'
    finally:
        sys.stdout = old_stdout


@pytest.mark.asyncio
async def test_mcp_stdio_small_caps_acquisition_bounds(monkeypatch):
    """R81 regression: caps 3, 4, 64 bound acquired UTF-8 bytes to <= cap + 1.

    Caps < 3 are rejected at construction (see test_mcp_server_constructor_validation).
    """
    import io

    class ObservedText:
        def __init__(self, content: str) -> None:
            self.stream = io.TextIOWrapper(io.BytesIO(content.encode("utf-8")), encoding="utf-8")
            self.sizes: list[int] = []

        def readline(self, n: int = -1) -> str:
            value = self.stream.readline(n)
            self.sizes.append(len(value.encode("utf-8")))
            return value

    for cap in [3, 4, 64]:
        source = ObservedText("🌟\n")
        monkeypatch.setattr(sys, "stdin", source)
        s = MCPServer(max_frame_bytes=cap)
        await s._read_bounded_line_stdio()
        assert max(source.sizes) <= cap + 1


@pytest.mark.asyncio
async def test_mcp_server_cancel_close_during_read_wait():
    """R82 regression: cancelling close during read wait does not produce false success

    or close registry while read task is still pending.
    """

    class MockRegistry:
        def __init__(self) -> None:
            self.closes = 0

        async def aclose(self) -> None:
            self.closes += 1

    class ResistantReader(asyncio.StreamReader):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.cancelled = asyncio.Event()
            self.release = asyncio.Event()

        async def read(self, n: int = -1) -> bytes:
            self.entered.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
            return b""

    rr = ResistantReader()
    reg = MockRegistry()
    server = MCPServer(registry=reg)
    owner = asyncio.create_task(server.run(rr))
    close_task: asyncio.Task[bool] | None = None
    try:
        await asyncio.wait_for(rr.entered.wait(), 1.0)
        close_task = asyncio.create_task(server.aclose())
        await asyncio.wait_for(rr.cancelled.wait(), 1.0)
        close_task.cancel()
        outcome = await asyncio.wait_for(asyncio.gather(close_task, return_exceptions=True), 1.0)
        assert str(outcome[0]) != "True"
        assert reg.closes == 0
        assert server._read_task is not None and not server._read_task.done()
        assert server._closed is False
    finally:
        rr.release.set()
        if close_task is not None:
            await asyncio.wait_for(asyncio.gather(close_task, return_exceptions=True), 2.0)
        await asyncio.wait_for(asyncio.gather(owner, return_exceptions=True), 2.0)
        if server._read_task is not None:
            await asyncio.wait_for(asyncio.gather(server._read_task, return_exceptions=True), 2.0)


@pytest.mark.asyncio
async def test_mcp_server_delayed_registry_close_deadline():
    """R83 regression: registry shutdown obeys a finite deadline and does not hang indefinitely.

    Registry-only observation keeps its 0.5s stage cap within the 1.1s total budget.
    """

    class DelayedRegistry:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.closes = 0

        async def aclose(self) -> None:
            self.closes += 1
            self.entered.set()
            await self.release.wait()

    reg = DelayedRegistry()
    server = MCPServer(registry=reg)
    close_task = asyncio.create_task(server.aclose())
    try:
        await asyncio.wait_for(reg.entered.wait(), 1.0)
        # Registry-only observation has a 0.5s stage cap; allow scheduling slack.
        done, _ = await asyncio.wait({close_task}, timeout=0.85)
        assert close_task in done
        assert close_task.result() is False
        assert server._closed is False
        assert server._registry_close_task is not None
        assert not server._registry_close_task.done()
        assert reg.closes == 1
    finally:
        reg.release.set()
        await asyncio.wait_for(asyncio.gather(close_task, return_exceptions=True), 2.0)
        if server._registry_close_task is not None:
            await asyncio.wait_for(
                asyncio.gather(server._registry_close_task, return_exceptions=True), 2.0
            )


class _LifetimeRegistry:
    def __init__(self, result: str = "success") -> None:
        self.result = result
        self.calls = 0
        self.active = 0
        self.peak = 0
        self.closed = False
        self.entered = asyncio.Event()
        self.duplicate_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def aclose(self) -> None:
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.closed = True
        self.entered.set()
        if self.calls > 1:
            self.duplicate_entered.set()
        try:
            await self.release.wait()
            if self.result == "error":
                raise RuntimeError("controlled late cleanup failure")
            if self.result == "cancelled":
                raise asyncio.CancelledError("cleanup itself cancelled")
        finally:
            self.active -= 1


def _lifetime_server(registry: Any) -> MCPServer:
    return MCPServer(registry=registry)


def _assert_lifetime_pending(
    server: MCPServer,
    registry: _LifetimeRegistry,
    outcome: object,
    identity: asyncio.Task[None] | None = None,
) -> asyncio.Task[None]:
    assert outcome is False
    assert server._closed is False
    assert registry.calls == 1
    assert registry.peak == 1
    task = server._registry_close_task
    assert task is not None
    assert not task.done()
    if identity is not None:
        assert task is identity
    return task


async def _finish_lifetime(
    server: MCPServer, registry: _LifetimeRegistry, *extra: asyncio.Task[Any]
) -> None:
    registry.release.set()
    tasks = set(extra)
    if server._registry_close_task is not None:
        tasks.add(server._registry_close_task)
    if server._put_task is not None:
        tasks.add(server._put_task)
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2.0)


@pytest.mark.asyncio
async def test_mcp_registry_retention_retry_early_closed_flag() -> None:
    registry = _LifetimeRegistry()
    server = _lifetime_server(registry)
    try:
        first = await asyncio.wait_for(server.aclose(), 1.4)
        identity = _assert_lifetime_pending(server, registry, first)
        assert registry.closed is True
        second = await asyncio.wait_for(server.aclose(), 1.4)
        _assert_lifetime_pending(server, registry, second, identity)
        registry.release.set()
        await asyncio.wait_for(server._registry_close_done.wait(), 1.0)
        assert await asyncio.wait_for(server.aclose(), 1.4) is True
        assert server._registry_close_task is identity
        assert identity.done()
        assert await asyncio.wait_for(server.aclose(), 1.4) is True
        assert registry.calls == registry.peak == 1
    finally:
        await _finish_lifetime(server, registry)


@pytest.mark.asyncio
async def test_mcp_registry_late_exception_without_retry() -> None:
    registry = _LifetimeRegistry("error")
    server = _lifetime_server(registry)
    try:
        first = await asyncio.wait_for(server.aclose(), 1.4)
        identity = _assert_lifetime_pending(server, registry, first)
        registry.release.set()
        await asyncio.wait_for(server._registry_close_done.wait(), 1.0)
        assert isinstance(server._registry_close_result, RuntimeError)
        assert str(server._registry_close_result) == "controlled late cleanup failure"
        # Check retrieval BEFORE any retry or test-side await of the failed task.
        assert getattr(identity, "_log_traceback", None) is False
        assert await asyncio.wait_for(server.aclose(), 1.4) is False
        assert server._registry_close_task is identity
        assert registry.calls == 1
        assert server._closed is False
    finally:
        await _finish_lifetime(server, registry)


@pytest.mark.asyncio
async def test_mcp_registry_cancel_close_observer() -> None:
    registry = _LifetimeRegistry()
    server = _lifetime_server(registry)
    caller = asyncio.create_task(server.aclose())
    try:
        await asyncio.wait_for(registry.entered.wait(), 1.0)
        identity = server._registry_close_task
        assert identity is not None
        caller.cancel()
        results = await asyncio.wait_for(asyncio.gather(caller, return_exceptions=True), 1.4)
        result = results[0]
        assert isinstance(result, asyncio.CancelledError) or result is False
        _assert_lifetime_pending(server, registry, False, identity)
        registry.release.set()
        await asyncio.wait_for(server._registry_close_done.wait(), 1.0)
        assert await asyncio.wait_for(server.aclose(), 1.4) is True
        assert server._registry_close_task is identity
        assert registry.calls == 1
    finally:
        await _finish_lifetime(server, registry, caller)


@pytest.mark.asyncio
async def test_mcp_registry_cleanup_itself_cancelled() -> None:
    registry = _LifetimeRegistry("cancelled")
    registry.release.set()
    server = _lifetime_server(registry)
    try:
        assert await asyncio.wait_for(server.aclose(), 1.4) is False
        identity = server._registry_close_task
        assert identity is not None and identity.done()
        await asyncio.wait_for(server._registry_close_done.wait(), 1.0)
        assert isinstance(server._registry_close_result, asyncio.CancelledError)
        assert await asyncio.wait_for(server.aclose(), 1.4) is False
        assert server._registry_close_task is identity
        assert registry.calls == 1
        assert server._closed is False
    finally:
        await _finish_lifetime(server, registry)


@pytest.mark.asyncio
async def test_mcp_registry_pending_put_dependency() -> None:
    registry = _LifetimeRegistry()
    registry.release.set()
    server = _lifetime_server(registry)
    put_entered = asyncio.Event()
    put_release = asyncio.Event()

    async def resistant_put() -> None:
        put_entered.set()
        while not put_release.is_set():
            try:
                await put_release.wait()
            except asyncio.CancelledError:
                pass

    server._put_task = asyncio.create_task(resistant_put())
    try:
        await asyncio.wait_for(put_entered.wait(), 1.0)
        assert await asyncio.wait_for(server.aclose(), 1.4) is False
        assert not server._put_task.done()
        assert registry.calls == 0
        assert server._closed is False
        put_release.set()
        await asyncio.wait_for(server._put_task, 2.0)
        assert await asyncio.wait_for(server.aclose(), 1.4) is True
        assert registry.calls == 1
        assert server._closed is True
    finally:
        put_release.set()
        await _finish_lifetime(server, registry)


@pytest.mark.asyncio
async def test_mcp_registry_stage_and_queued_deadlines() -> None:
    registry = _LifetimeRegistry()
    server = _lifetime_server(registry)
    callers = [asyncio.create_task(server.aclose()) for _ in range(4)]
    try:
        done, pending = await asyncio.wait(callers, timeout=1.4)
        assert not pending and len(done) == 4
        identity = server._registry_close_task
        assert identity is not None
        for caller in callers:
            _assert_lifetime_pending(server, registry, caller.result(), identity)
        registry.release.set()
        await asyncio.wait_for(server._registry_close_done.wait(), 1.0)
        assert await asyncio.wait_for(server.aclose(), 1.4) is True
        assert server._registry_close_task is identity
        assert registry.calls == registry.peak == 1
    finally:
        for caller in callers:
            if not caller.done():
                caller.cancel()
        await _finish_lifetime(server, registry, *callers)


async def _check_lifetime_negative(fault: str) -> None:
    registry = _LifetimeRegistry()
    server = _lifetime_server(registry)
    identity: asyncio.Task[None] | None = None
    extra: list[asyncio.Task[Any]] = []
    try:
        if fault == "cancel-false-success":

            async def falsely_successful_observer() -> bool:
                try:
                    return await server.aclose()
                except asyncio.CancelledError:
                    return True

            caller = asyncio.create_task(falsely_successful_observer())
            extra.append(caller)
            await asyncio.wait_for(registry.entered.wait(), 1.0)
            identity = server._registry_close_task
            caller.cancel()
            outcome = await asyncio.wait_for(caller, 1.4)
        else:
            outcome = await asyncio.wait_for(server.aclose(), 1.4)
            identity = _assert_lifetime_pending(server, registry, outcome)
            if fault == "missing-reference":
                server._registry_close_task = None
            elif fault == "duplicate-call":
                duplicate = asyncio.create_task(registry.aclose())
                extra.append(duplicate)
                await asyncio.wait_for(registry.duplicate_entered.wait(), 1.0)
            elif fault == "false-success":
                outcome = True
            else:
                raise AssertionError("unknown controlled fault")
        # The same helper passes real behavior and rejects each actual corruption.
        with pytest.raises(AssertionError):
            _assert_lifetime_pending(server, registry, outcome, identity)
    finally:
        if identity is not None:
            server._registry_close_task = identity
        await _finish_lifetime(server, registry, *extra)


@pytest.mark.asyncio
async def test_mcp_negative_missing_retained_reference() -> None:
    await _check_lifetime_negative("missing-reference")


@pytest.mark.asyncio
async def test_mcp_negative_duplicated_cleanup_call() -> None:
    await _check_lifetime_negative("duplicate-call")


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["false-success", "cancel-false-success"])
async def test_mcp_negative_clean_true_while_cleanup_pending(fault: str) -> None:
    await _check_lifetime_negative(fault)
