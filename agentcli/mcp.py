"""Model Context Protocol (MCP) server implementation for agentcli (Phase 7).

Exposes agentcli tools and sub-agents over standard JSON-RPC 2.0 stdio, enabling
integration with external agent hosts such as Claude Desktop, Antigravity, Cursor, etc.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from .agent.registry import ToolRegistry

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"


class MCPServer:
    """Lightweight stdio JSON-RPC 2.0 server implementing Model Context Protocol tools subset."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or ToolRegistry()

    def _tool_definitions(self) -> list[dict[str, Any]]:
        """Return MCP-compliant tool definitions for all registered tools."""
        definitions = []
        for name in self.registry.registered_types():
            desc = f"agentcli tool: {name}"
            if name == "file_ops":
                desc = "Perform file operations: read, write, or list files."
            elif name == "shell_execution":
                desc = "Execute sandboxed shell commands."
            elif name == "code_analyzer":
                desc = "Inspect and analyze code files."
            elif name == "web_search":
                desc = "Query and retrieve external information."

            definitions.append(
                {
                    "name": name,
                    "description": desc,
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "operation": {"type": "string"},
                            "path": {"type": "string"},
                            "command": {"type": "string"},
                            "query": {"type": "string"},
                            "task": {"type": "string"},
                        },
                    },
                }
            )
        return definitions

    async def handle_request(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Process a single JSON-RPC message and return the response dictionary if applicable."""
        msg_id = message.get("id")
        method = message.get("method")
        params = message.get("params", {})

        if not method:
            return None

        # Notifications (no response expected)
        if method.startswith("notifications/"):
            return None

        # Standard RPC methods
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {
                            "listChanged": False,
                        },
                    },
                    "serverInfo": {
                        "name": "agentcli",
                        "version": "0.1.0",
                    },
                },
            }

        if method == "ping":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {},
            }

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": self._tool_definitions(),
                },
            }

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = await self.registry.execute(tool_name, arguments)
            text_out = json.dumps(
                result.output if result.output is not None else {"error": result.error}, indent=2
            )
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": text_out,
                        }
                    ],
                    "isError": not result.success,
                },
            }

        # Unknown method
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}",
            },
        }

    async def run(self, reader: asyncio.StreamReader | None = None) -> None:
        """Run the JSON-RPC message processing loop on stdio or custom reader."""
        if reader is None:
            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            line = await reader.readline()
            if not line:
                break
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue

            try:
                msg = json.loads(line_str)
            except json.JSONDecodeError:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()
                continue

            response = await self.handle_request(msg)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()


def run_mcp(registry: ToolRegistry | None = None) -> int:
    """Entry point for `agentcli mcp` command."""
    server = MCPServer(registry=registry)
    try:
        asyncio.run(server.run())
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("MCP server failed: %s", exc)
        return 1
