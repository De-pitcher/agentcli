"""Comprehensive unit and integration tests for Phase 19: MCP Client & External Tool Integrations."""

from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcli.agent.registry import ToolRegistry
from agentcli.config import Config, MCPServerConfig
from agentcli.mcp.adapter import MCPToolAgent, mcp_tool_to_openrouter_schema
from agentcli.mcp.client import (
    MCP_PROTOCOL_VERSION,
    MCPClient,
    MCPClientError,
    MCPClientTimeoutError,
)
from agentcli.mcp.manager import MCPClientManager
from agentcli.subagents.base import SubAgentTask, SubAgentType

# ---------------------------------------------------------------------------
# Unit Tests: Schema Conversion
# ---------------------------------------------------------------------------


def test_mcp_tool_to_openrouter_schema() -> None:
    mcp_tool = {
        "name": "fetch_weather",
        "description": "Fetch current weather for a city",
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name"},
            },
            "required": ["city"],
        },
    }
    schema = mcp_tool_to_openrouter_schema(mcp_tool)
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "fetch_weather"
    assert schema["function"]["description"] == "Fetch current weather for a city"
    assert "city" in schema["function"]["parameters"]["properties"]
    assert schema["function"]["parameters"]["required"] == ["city"]


def test_mcp_tool_to_openrouter_schema_defaults() -> None:
    mcp_tool = {"name": "simple_tool"}
    schema = mcp_tool_to_openrouter_schema(mcp_tool)
    assert schema["function"]["name"] == "simple_tool"
    assert "simple_tool" in schema["function"]["description"]
    assert schema["function"]["parameters"]["type"] == "object"


# ---------------------------------------------------------------------------
# Unit Tests: MCPToolAgent Adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_tool_agent_success() -> None:
    mock_client = MagicMock()
    mock_client.name = "test_srv"
    mock_client.call_tool = AsyncMock(
        return_value={
            "content": [{"type": "text", "text": json.dumps({"temperature": 22, "unit": "celsius"})}],
            "isError": False,
        }
    )

    agent = MCPToolAgent(
        client=mock_client,
        tool_name="get_weather",
        server_name="test_srv",
        description="Get weather info",
    )
    task = SubAgentTask(
        agent_type=SubAgentType.WORKSPACE,
        payload={"city": "London"},
    )
    res = await agent.run(task)

    assert res.success is True
    assert isinstance(res.output, dict)
    assert res.output.get("temperature") == 22
    mock_client.call_tool.assert_called_once_with("get_weather", {"city": "London"})


@pytest.mark.asyncio
async def test_mcp_tool_agent_is_error() -> None:
    mock_client = MagicMock()
    mock_client.name = "test_srv"
    mock_client.call_tool = AsyncMock(
        return_value={
            "content": [{"type": "text", "text": "City not found"}],
            "isError": True,
        }
    )

    agent = MCPToolAgent(client=mock_client, tool_name="get_weather")
    task = SubAgentTask(agent_type=SubAgentType.WORKSPACE, payload={"city": "Atlantis"})
    res = await agent.run(task)

    assert res.success is False
    assert "City not found" in str(res.error)


@pytest.mark.asyncio
async def test_mcp_tool_agent_client_exception() -> None:
    mock_client = MagicMock()
    mock_client.name = "test_srv"
    mock_client.call_tool = AsyncMock(side_effect=MCPClientError("Connection reset"))

    agent = MCPToolAgent(client=mock_client, tool_name="bad_tool")
    task = SubAgentTask(agent_type=SubAgentType.WORKSPACE, payload={})
    res = await agent.run(task)

    assert res.success is False
    assert "Connection reset" in str(res.error)


# ---------------------------------------------------------------------------
# Unit Tests: MCPClient Mocked Stdio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_client_not_connected_errors() -> None:
    client = MCPClient(name="test", command="echo")
    assert client.is_connected is False

    with pytest.raises(MCPClientError, match="not connected"):
        await client.list_tools()

    with pytest.raises(MCPClientError, match="not connected"):
        await client.call_tool("tool", {})


@pytest.mark.asyncio
async def test_mcp_client_handshake_and_tool_call() -> None:
    client = MCPClient(name="test", command="dummy", timeout_seconds=2.0)

    response_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def handle_write(data: bytes) -> None:
        try:
            req = json.loads(data.decode("utf-8"))
            req_id = req.get("id")
            method = req.get("method")
            if method == "initialize":
                response_queue.put_nowait(
                    json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": MCP_PROTOCOL_VERSION}}).encode("utf-8") + b"\n"
                )
            elif method == "tools/list":
                response_queue.put_nowait(
                    json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"tools": [{"name": "echo_tool", "description": "Echoes text"}]}}).encode("utf-8") + b"\n"
                )
            elif method == "tools/call":
                response_queue.put_nowait(
                    json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": "echoed"}], "isError": False}}).encode("utf-8") + b"\n"
                )
        except Exception:
            pass

    # Mock subprocess streams
    mock_stdin = MagicMock()
    mock_stdin.write = MagicMock(side_effect=handle_write)
    mock_stdin.drain = AsyncMock()
    mock_stdin.close = MagicMock()
    mock_stdin.wait_closed = AsyncMock()

    mock_stdout = MagicMock()
    mock_stdout.readline = AsyncMock(side_effect=response_queue.get)

    mock_proc = MagicMock()
    mock_proc.stdin = mock_stdin
    mock_proc.stdout = mock_stdout
    mock_proc.returncode = None
    mock_proc.wait = AsyncMock(return_value=0)
    mock_proc.terminate = MagicMock()

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        init_res = await client.connect()
        assert client.is_connected is True
        assert init_res.get("protocolVersion") == MCP_PROTOCOL_VERSION

        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "echo_tool"

        call_res = await client.call_tool("echo_tool", {"msg": "hi"})
        assert call_res.get("isError") is False
        assert call_res["content"][0]["text"] == "echoed"

        await client.close()
        assert client.is_connected is False


@pytest.mark.asyncio
async def test_mcp_client_timeout() -> None:
    client = MCPClient(name="slow_server", command="dummy", timeout_seconds=0.05)

    mock_stdin = MagicMock()
    mock_stdin.write = MagicMock()
    mock_stdin.drain = AsyncMock()

    async def _hang():
        await asyncio.sleep(10)
        return b""

    mock_stdout = MagicMock()
    mock_stdout.readline = AsyncMock(side_effect=_hang)

    mock_proc = MagicMock()
    mock_proc.stdin = mock_stdin
    mock_proc.stdout = mock_stdout
    mock_proc.returncode = None
    mock_proc.wait = AsyncMock(return_value=0)
    mock_proc.terminate = MagicMock()

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        # Handshake will time out
        with pytest.raises(MCPClientTimeoutError):
            await client.connect()

        await client.close()


# ---------------------------------------------------------------------------
# Unit Tests: MCPClientManager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_client_manager_registration() -> None:
    config = Config()
    config.mcp_servers["test_service"] = MCPServerConfig(
        name="test_service",
        command="python",
        args=["-m", "dummy"],
        enabled=True,
    )
    config.mcp_servers["disabled_service"] = MCPServerConfig(
        name="disabled_service",
        command="python",
        enabled=False,
    )

    manager = MCPClientManager(config=config)

    # Mock client connection and tool listing
    mock_client = MagicMock()
    mock_client.name = "test_service"
    mock_client.connect = AsyncMock(return_value={})
    mock_client.list_tools = AsyncMock(
        return_value=[
            {"name": "query_db", "description": "Run SQL query", "inputSchema": {"type": "object"}},
            {"name": "insert_db", "description": "Insert record", "inputSchema": {"type": "object"}},
        ]
    )
    mock_client.call_tool = AsyncMock(return_value={"content": [{"type": "text", "text": "result"}], "isError": False})
    mock_client.close = AsyncMock()

    with patch("agentcli.mcp.manager.MCPClient", return_value=mock_client):
        await manager.initialize()
        assert "test_service" in manager.clients
        assert "disabled_service" not in manager.clients

        # Verify tool definitions export
        defs = manager.get_tool_definitions()
        assert len(defs) == 2
        names = {d["function"]["name"] for d in defs}
        assert names == {"query_db", "insert_db"}

        # Verify ToolRegistry integration
        registry = ToolRegistry()
        manager.register_tools(registry)
        assert "query_db" in registry.registered_types()
        assert "insert_db" in registry.registered_types()

        # Execute registered tool through registry
        res = await registry.execute("query_db", {"sql": "SELECT 1"})
        assert res.success is True

        await manager.aclose()
        assert len(manager.clients) == 0


# ---------------------------------------------------------------------------
# Integration Tests: End-to-End Live MCP Server <-> Client Subprocess
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_client_server_live_e2e() -> None:
    """Test full live subprocess communication between MCPClient and agentcli MCPServer."""
    client = MCPClient(
        name="live_agentcli_mcp",
        command=sys.executable,
        args=["-m", "agentcli", "mcp"],
        timeout_seconds=10.0,
    )

    try:
        init_res = await client.connect()
        assert init_res.get("protocolVersion") == MCP_PROTOCOL_VERSION
        assert init_res.get("serverInfo", {}).get("name") == "agentcli"

        tools = await client.list_tools()
        assert len(tools) >= 4
        tool_names = [t["name"] for t in tools]
        assert "file_ops" in tool_names
        assert "shell_execution" in tool_names

        # Call shell_execution tool live through the MCP subprocess
        call_res = await client.call_tool(
            "shell_execution",
            {"command": "python -c \"print('mcp_e2e_verified')\""},
        )
        assert call_res.get("isError") is False
        output_text = call_res.get("content", [])[0].get("text", "")
        assert "mcp_e2e_verified" in output_text

    finally:
        await client.close()
