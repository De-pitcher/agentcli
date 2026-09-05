"""Model Context Protocol (MCP) server and client ecosystem for agentcli (Phases 7 & 19)."""

from __future__ import annotations

from .adapter import MCPToolAgent, mcp_tool_to_openrouter_schema
from .client import MCP_PROTOCOL_VERSION, MCPClient, MCPClientError, MCPClientTimeoutError
from .manager import MCPClientManager
from .server import MCPServer, run_mcp

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "MCPClient",
    "MCPClientError",
    "MCPClientManager",
    "MCPClientTimeoutError",
    "MCPServer",
    "MCPToolAgent",
    "mcp_tool_to_openrouter_schema",
    "run_mcp",
]
