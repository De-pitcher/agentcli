import pytest

from agentcli.agent.registry import ToolRegistry
from agentcli.mcp import MCPServer, run_mcp


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
    test_file = tmp_path / "hello.txt"
    test_file.write_text("file content", encoding="utf-8")

    # Read-only default (no allow_write)
    registry = ToolRegistry(tool_configs={"file_ops": {"working_dir": str(tmp_path)}})
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


@pytest.mark.asyncio
async def test_mcp_default_shell_execution_happy_and_denial():
    server = MCPServer()

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
    resp_happy = await server.handle_request(req_happy)
    assert resp_happy["result"]["isError"] is False
    assert "mcp_ok" in resp_happy["result"]["content"][0]["text"]

    # Denial path: command in denylist
    req_denial = {
        "jsonrpc": "2.0",
        "id": 202,
        "method": "tools/call",
        "params": {
            "name": "shell_execution",
            "arguments": {"command": "rm -rf /"},
        },
    }
    resp_denial = await server.handle_request(req_denial)
    assert resp_denial["result"]["isError"] is True
    assert "denied" in resp_denial["result"]["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_mcp_default_code_analyzer_happy_and_denial(tmp_path):
    server = MCPServer()

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

    # Denial path: nonexistent file
    req_denial = {
        "jsonrpc": "2.0",
        "id": 302,
        "method": "tools/call",
        "params": {
            "name": "code_analyzer",
            "arguments": {"files": ["/path/does/not/exist/never.py"]},
        },
    }
    resp_denial = await server.handle_request(req_denial)
    assert resp_denial["result"]["isError"] is True
    assert "Failed to read" in resp_denial["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_mcp_default_web_search_happy_and_denial(monkeypatch):
    server = MCPServer()

    # Happy path with mock provider
    from agentcli.subagents.web_search import SearchResult

    async def mock_search(self, query, max_results=10):
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
    assert "No search query" in resp_denial["result"]["content"][0]["text"]
