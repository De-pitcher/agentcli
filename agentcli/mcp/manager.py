"""Manager for external MCP client lifecycles and tool registration (Phase 19)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from .adapter import MCPToolAgent, mcp_tool_to_openrouter_schema
from .client import MCPClient

if TYPE_CHECKING:
    from ..agent.registry import ToolRegistry
    from ..config import Config

logger = logging.getLogger(__name__)


class MCPClientManager:
    """Manages connections to multiple external MCP servers and registers their tools."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config
        self._clients: dict[str, MCPClient] = {}
        self._tools_by_server: dict[str, list[dict[str, Any]]] = {}
        self._tool_to_server_map: dict[str, str] = {}
        self._is_initialized = False

    @property
    def clients(self) -> dict[str, MCPClient]:
        """Return currently active MCP clients keyed by server name."""
        return self._clients

    async def initialize(self) -> None:
        """Start and initialize all configured and enabled MCP servers."""
        if self._is_initialized or self.config is None:
            return

        for name, s_cfg in self.config.mcp_servers.items():
            if not s_cfg.enabled or not s_cfg.command:
                continue

            logger.info("Initializing MCP server connection: '%s' (%s)", name, s_cfg.command)
            client = MCPClient(
                name=name,
                command=s_cfg.command,
                args=s_cfg.args,
                env=s_cfg.env,
            )
            try:
                await client.connect()
                tools = await client.list_tools()
                self._clients[name] = client
                self._tools_by_server[name] = tools
                for tool in tools:
                    t_name = tool.get("name")
                    if t_name:
                        self._tool_to_server_map[t_name] = name
                logger.info(
                    "MCP server '%s' connected successfully (%d tool(s) discovered)",
                    name,
                    len(tools),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to connect to MCP server '%s': %s", name, exc)
                await client.close()

        self._is_initialized = True

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return OpenRouter/OpenAI-compatible tool definitions for all discovered MCP tools."""
        definitions: list[dict[str, Any]] = []
        for tools in self._tools_by_server.values():
            for tool in tools:
                definitions.append(mcp_tool_to_openrouter_schema(tool))
        return definitions

    def register_tools(self, registry: ToolRegistry) -> None:
        """Register all discovered MCP tools into a ToolRegistry instance."""
        for server_name, tools in self._tools_by_server.items():
            client = self._clients.get(server_name)
            if client is None:
                continue

            for tool in tools:
                t_name = tool.get("name")
                if not t_name:
                    continue

                desc = tool.get("description", f"MCP Tool from {server_name}")
                # Create a factory closure for this specific tool
                def make_factory(c: MCPClient, name: str, s_name: str, d: str) -> Callable[[], MCPToolAgent]:
                    return lambda: MCPToolAgent(client=c, tool_name=name, server_name=s_name, description=d)

                factory = make_factory(client, t_name, server_name, desc)
                registry.register(t_name, factory)
                logger.debug("Registered external MCP tool '%s' into ToolRegistry", t_name)

    async def aclose(self) -> None:
        """Close all running MCP client connections."""
        for name, client in list(self._clients.items()):
            try:
                await client.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error closing MCP client '%s': %s", name, exc)
        self._clients.clear()
        self._tools_by_server.clear()
        self._tool_to_server_map.clear()
        self._is_initialized = False
