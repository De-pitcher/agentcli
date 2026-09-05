"""Model Context Protocol (MCP) async client implementation (Phase 19).

Enables agentcli to connect to external MCP servers over stdio (subprocess)
and consume their tools seamlessly inside the Plan → Act → Reflect agent loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"


class MCPClientError(Exception):
    """Base exception for MCP client errors."""


class MCPClientTimeoutError(MCPClientError):
    """Raised when an MCP server request times out."""


class MCPClient:
    """Asynchronous JSON-RPC 2.0 client for connecting to an external MCP server."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.timeout_seconds = timeout_seconds

        self._proc: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._pending_requests: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._is_connected = False
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        """Return True if the client is actively connected and running."""
        return self._is_connected and self._proc is not None and self._proc.returncode is None

    async def connect(self) -> dict[str, Any]:
        """Spawn the server process, perform handshake, and return server metadata."""
        async with self._lock:
            if self.is_connected:
                return {}

            cmd = [self.command, *self.args]
            run_env = os.environ.copy()
            run_env.update(self.env)

            logger.debug("[%s] Spawning MCP server process: %s", self.name, cmd)
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=run_env,
                )
            except Exception as exc:
                raise MCPClientError(f"Failed to spawn MCP server '{self.name}' ({cmd}): {exc}") from exc

            self._is_connected = True
            self._reader_task = asyncio.create_task(self._read_responses())

            # Perform initialize handshake
            init_resp = await self._send_request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "agentcli", "version": "2.0.0"},
                },
            )

            # Send initialized notification
            await self._send_notification("notifications/initialized", {})

            res = init_resp.get("result", {})
            if isinstance(res, dict):
                return res
            return {}

    async def list_tools(self) -> list[dict[str, Any]]:
        """Fetch the list of tools exposed by the MCP server."""
        resp = await self._send_request("tools/list", {})
        result = resp.get("result", {})
        tools: list[dict[str, Any]] = result.get("tools", [])
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool on the external MCP server."""
        resp = await self._send_request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        if "error" in resp:
            err = resp["error"]
            raise MCPClientError(f"MCP tool '{name}' error: {err.get('message', err)}")

        result: dict[str, Any] = resp.get("result", {})
        return result

    async def ping(self) -> bool:
        """Send a ping request to verify server responsiveness."""
        try:
            await self._send_request("ping", {})
            return True
        except Exception:  # noqa: BLE001
            return False

    async def close(self) -> None:
        """Gracefully terminate the MCP server process and clean up resources."""
        async with self._lock:
            self._is_connected = False

            # Cancel any pending requests
            for fut in self._pending_requests.values():
                if not fut.done():
                    fut.cancel()
            self._pending_requests.clear()

            if self._reader_task and not self._reader_task.done():
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except asyncio.CancelledError:
                    pass

            if self._proc is not None:
                try:
                    if self._proc.stdin:
                        self._proc.stdin.close()
                        await self._proc.stdin.wait_closed()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Error closing stdin for MCP server %s: %s", self.name, exc)

                try:
                    self._proc.terminate()
                    try:
                        await asyncio.wait_for(self._proc.wait(), timeout=3.0)
                    except TimeoutError:
                        self._proc.kill()
                        await self._proc.wait()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Error terminating process for MCP server %s: %s", self.name, exc)
                self._proc = None

    # ------------------------------------------------------------------
    # Internal JSON-RPC messaging
    # ------------------------------------------------------------------

    async def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.is_connected or self._proc is None or self._proc.stdin is None:
            raise MCPClientError(f"MCP server '{self.name}' is not connected.")

        self._request_id += 1
        req_id = self._request_id
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_requests[req_id] = future

        msg_bytes = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            self._proc.stdin.write(msg_bytes)
            await self._proc.stdin.drain()
        except Exception as exc:
            self._pending_requests.pop(req_id, None)
            raise MCPClientError(f"Failed to write to MCP server '{self.name}': {exc}") from exc

        try:
            return await asyncio.wait_for(future, timeout=self.timeout_seconds)
        except TimeoutError as exc:
            self._pending_requests.pop(req_id, None)
            raise MCPClientTimeoutError(
                f"MCP server '{self.name}' request '{method}' timed out after {self.timeout_seconds}s"
            ) from exc

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        if not self.is_connected or self._proc is None or self._proc.stdin is None:
            return

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        msg_bytes = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            self._proc.stdin.write(msg_bytes)
            await self._proc.stdin.drain()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] Failed to send notification: %s", self.name, exc)

    async def _read_responses(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            return

        while self._is_connected:
            try:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                try:
                    data = json.loads(line_str)
                except json.JSONDecodeError:
                    logger.debug("[%s] Received malformed JSON-RPC: %s", self.name, line_str)
                    continue

                req_id = data.get("id")
                if req_id is not None and req_id in self._pending_requests:
                    fut = self._pending_requests.pop(req_id)
                    if not fut.done():
                        fut.set_result(data)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.debug("[%s] Error reading from MCP server: %s", self.name, exc)
                break
