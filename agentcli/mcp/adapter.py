"""Adapter connecting external MCP tools to agentcli subagent execution (Phase 19)."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..subagents.base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType
from .client import MCPClient, MCPClientError

logger = logging.getLogger(__name__)


def mcp_tool_to_openrouter_schema(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert an MCP tool definition to OpenAI/OpenRouter function calling format.

    Args:
        tool: An MCP tool object with 'name', 'description', and 'inputSchema'.

    Returns:
        A dict matching the OpenAI function calling schema standard.
    """
    name = tool.get("name", "")
    description = tool.get("description", f"External MCP tool: {name}")
    input_schema = tool.get("inputSchema") or {
        "type": "object",
        "properties": {},
    }

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": input_schema,
        },
    }


class MCPToolAgent(SubAgent):
    """SubAgent adapter that forwards execution to an external MCP server."""

    def __init__(
        self,
        client: MCPClient,
        tool_name: str,
        server_name: str = "",
        description: str = "",
    ) -> None:
        # Use workspace / generic subagent type for external tools
        super().__init__(SubAgentType.WORKSPACE)
        self.client = client
        self.tool_name = tool_name
        self.server_name = server_name or client.name
        self.tool_description = description

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Forward task payload as arguments to the external MCP server tool."""
        arguments = dict(task.payload or {})
        logger.debug(
            "Executing external MCP tool '%s' on server '%s' with args: %s",
            self.tool_name,
            self.server_name,
            list(arguments.keys()),
        )

        try:
            result = await self.client.call_tool(self.tool_name, arguments)
            is_error = bool(result.get("isError", False))
            content_items = result.get("content", [])

            # Parse content blocks (text / image / json)
            text_blocks: list[str] = []
            for item in content_items:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        text_blocks.append(str(item.get("text", "")))
                    else:
                        text_blocks.append(json.dumps(item))
                else:
                    text_blocks.append(str(item))

            output_text = "\n".join(text_blocks).strip()
            
            # Try parsing as JSON dict if possible
            output_obj: Any = output_text
            try:
                output_obj = json.loads(output_text)
            except (json.JSONDecodeError, ValueError):
                pass

            if is_error:
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=task.agent_type,
                    success=False,
                    error=output_text or f"MCP tool '{self.tool_name}' returned error status.",
                )

            return SubAgentResult(
                task_id=task.id,
                agent_type=task.agent_type,
                success=True,
                output=output_obj if isinstance(output_obj, dict) else {"result": output_obj},
            )

        except MCPClientError as exc:
            return SubAgentResult(
                task_id=task.id,
                agent_type=task.agent_type,
                success=False,
                error=f"MCP execution error: {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            return SubAgentResult(
                task_id=task.id,
                agent_type=task.agent_type,
                success=False,
                error=f"Unexpected error executing MCP tool '{self.tool_name}': {exc}",
            )
