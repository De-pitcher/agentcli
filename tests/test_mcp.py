import pytest

from agentcli.agent.registry import ToolRegistry
from agentcli.mcp import MCPServer


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
