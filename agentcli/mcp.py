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
            input_schema = {
                "type": "object",
                "properties": {},
                "required": [],
            }
            if name == "file_ops":
                desc = "Perform file operations: read, write, create, delete, list, or mkdir. Paths are constrained to the working directory."
                input_schema = {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string", "enum": ["read", "write", "create", "delete", "list", "mkdir"]},
                        "path": {"type": "string", "description": "Path to file or directory"},
                        "content": {"type": "string", "description": "Content to write (required for write operation)"},
                        "encoding": {"type": "string", "description": "Text encoding (default: utf-8)"},
                    },
                    "required": ["operation", "path"],
                }
            elif name == "shell_execution":
                desc = "Execute sandboxed shell commands. Uses allowlist/denylist. No shell=True, direct binary execution."
                input_schema = {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Command to execute"},
                        "timeout": {"type": "number", "description": "Timeout in seconds (default: 30)"},
                        "working_dir": {"type": "string", "description": "Working directory (default: current directory)"},
                    },
                    "required": ["command"],
                }
            elif name == "code_analyzer":
                desc = "Analyze code files for bugs, security issues, performance problems, and style. Uses LLM for deep analysis when model provided."
                input_schema = {
                    "type": "object",
                    "properties": {
                        "files": {"type": "array", "items": {"type": "string"}, "description": "List of file paths to analyze"},
                        "focus": {"type": "string", "enum": ["security", "performance", "style", "general"], "default": "general"},
                        "context": {"type": "string", "description": "Additional context for the analysis"},
                        "model": {"type": "string", "description": "Optional model ID for LLM-based analysis"},
                        "models": {"type": "array", "items": {"type": "string"}, "description": "Optional list of model fallbacks"},
                    },
                    "required": ["files"],
                }
            elif name == "web_search":
                desc = "Search the web for information. Supports Brave Search API and DuckDuckGo fallback."
                input_schema = {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query string"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
                        "provider": {"type": "string", "enum": ["brave", "duckduckgo"], "description": "Search provider to use"},
                        "timeout": {"type": "number", "description": "Request timeout in seconds (default: 30)"},
                    },
                    "required": ["query"],
                }
            elif name == "file_ops":
                desc = "Perform file operations: read, write, create, delete, list, or mkdir. Paths are constrained to the working directory."
                input_schema = {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string", "enum": ["read", "write", "create", "delete", "list", "mkdir"]},
                        "path": {"type": "string", "description": "Path to file or directory"},
                        "content": {"type": "string", "description": "Content to write (required for write operation)"},
                        "encoding": {"type": "string", "description": "Text encoding (default: utf-8)"},
                    },
                    "required": ["operation", "path"],
                }
            else:
                input_schema = {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string"},
                        "path": {"type": "string"},
                        "command": {"type": "string"},
                        "query": {"type": "string"},
                        "task": {"type": "string"},
                    },
                }
            
            definitions.append(
                {
                    "name": name,
                    "description": desc,
                    "inputSchema": input_schema,
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
