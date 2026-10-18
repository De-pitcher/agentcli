"""Model Context Protocol (MCP) server implementation for agentcli (Phase 7 & Phase 19).

Exposes agentcli tools and sub-agents over standard JSON-RPC 2.0 stdio, enabling
integration with external agent hosts such as Claude Desktop, Antigravity, Cursor, etc.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from .. import __version__
from ..agent.registry import ToolRegistry

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"
MAX_FRAME_BYTES = 64 * 1024  # 64 KB max JSON-RPC message frame


class MCPServer:
    """Lightweight stdio JSON-RPC 2.0 server implementing Model Context Protocol tools subset."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        *,
        max_concurrency: int = 1,
        max_queue_size: int = 32,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> None:
        if (
            isinstance(max_concurrency, bool)
            or not isinstance(max_concurrency, int)
            or max_concurrency < 1
        ):
            raise ValueError(
                f"max_concurrency must be a positive integer >= 1, got {max_concurrency!r}"
            )
        if (
            isinstance(max_queue_size, bool)
            or not isinstance(max_queue_size, int)
            or max_queue_size < 1
        ):
            raise ValueError(
                f"max_queue_size must be a positive integer >= 1, got {max_queue_size!r}"
            )
        if (
            isinstance(max_frame_bytes, bool)
            or not isinstance(max_frame_bytes, int)
            or max_frame_bytes < 3
        ):
            raise ValueError(
                f"max_frame_bytes must be a positive integer >= 3, got {max_frame_bytes!r}"
            )

        self.registry = registry or ToolRegistry()
        self.max_concurrency = max_concurrency
        self.max_queue_size = max_queue_size
        self.max_frame_bytes = max_frame_bytes
        self._active_tasks: set[asyncio.Task[Any]] = set()
        self._closing = False
        self._closed = False
        self._close_lock: asyncio.Lock | None = None
        self._close_outcome: bool | None = None
        self._queue: asyncio.Queue[tuple[str, asyncio.Future[None]] | None] | None = None
        self._worker_tasks: list[asyncio.Task[Any]] = []
        self._run_task: asyncio.Task[Any] | None = None
        self._read_task: asyncio.Task[Any] | None = None
        self._put_task: asyncio.Task[Any] | None = None
        self._stop_event: asyncio.Event = asyncio.Event()
        self._reading_stdin: bool = False
        self._stdin_thread_active: bool = False
        # Registry cleanup lifetime tracking (task84-B)
        self._registry_close_task: asyncio.Task[None] | None = None
        self._registry_close_result: bool | BaseException | None = None
        self._registry_close_done: asyncio.Event = asyncio.Event()

    async def aclose(self) -> bool:
        """Drain or cancel in-flight tasks and close registry context.

        Never discards unfinished pending tasks from active tracking;
        retains ownership until completion or explicit failure reporting.
        Returns True if all tasks finished and registry was closed cleanly,
        or False if any task remains unresolved/pending.

        Per-call observation deadline: 1.1s total including lock acquisition.
        Registry cleanup task is created once and retained until completion.
        """
        # Per-call monotonic deadline (amendment 1)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 1.1

        if self._close_lock is None:
            self._close_lock = asyncio.Lock()

        # Bound lock acquisition by remaining time
        lock_acquired = False
        try:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.wait_for(self._close_lock.acquire(), timeout=remaining)
            lock_acquired = True

            # Fast path: already completed successfully
            if self._closed and self._close_outcome is True:
                return True

            self._closing = True
            self._stop_event.set()

            # --- Phase 1: Cancel and observe dependencies with remaining budget ---
            read_clean = True
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            # Cancel active read task if running with bounded wait
            if self._read_task is not None and not self._read_task.done():
                self._read_task.cancel()
                try:
                    done, _ = await asyncio.wait({self._read_task}, timeout=min(0.2, remaining))
                    if self._read_task not in done:
                        read_clean = False
                except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                    logger.debug("Non-fatal error cancelling read task: %s", exc)
                    read_clean = False

            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            if getattr(self, "_reading_stdin", False) or getattr(
                self, "_stdin_thread_active", False
            ):
                read_clean = False

            # Cancel active queue.put task if blocked
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            if self._put_task is not None and not self._put_task.done():
                self._put_task.cancel()
                try:
                    await asyncio.wait({self._put_task}, timeout=min(0.1, remaining))
                except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                    logger.debug("Non-fatal error cancelling put task: %s", exc)

            # Cancel the main run task if called externally (e.g. from signal or shutdown target)
            cur = asyncio.current_task()
            if (
                self._run_task is not None
                and not self._run_task.done()
                and self._run_task is not cur
            ):
                self._run_task.cancel()

            # Cancel all active handler tasks
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            if self._active_tasks:
                for t in list(self._active_tasks):
                    t.cancel()
                _done, _pending = await asyncio.wait(
                    self._active_tasks, timeout=min(0.1, remaining)
                )
                # Remove only completed tasks from active tracking; do NOT discard unfinished tasks!
                for t in _done:
                    self._active_tasks.discard(t)

            all_deps_clean = len(self._active_tasks) == 0 and read_clean

            # Signal background workers to stop and cancel them (excluding resistant active handler workers)
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            workers_to_cancel = [w for w in self._worker_tasks if w not in self._active_tasks]
            for w in workers_to_cancel:
                w.cancel()
            if workers_to_cancel:
                try:
                    await asyncio.wait(workers_to_cancel, timeout=min(0.2, remaining))
                except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                    logger.debug("Non-fatal error waiting for worker cancellation: %s", exc)

            # Drain queue
            if self._queue is not None:
                while not self._queue.empty():
                    try:
                        item = self._queue.get_nowait()
                        if item is not None:
                            _line, fut = item
                            if not fut.done():
                                fut.cancel()
                        self._queue.task_done()
                    except (asyncio.QueueEmpty, ValueError):
                        break

                for _ in range(len(self._worker_tasks)):
                    try:
                        self._queue.put_nowait(None)
                    except (asyncio.QueueFull, Exception) as exc:  # noqa: BLE001
                        logger.debug("Non-fatal error notifying worker shutdown: %s", exc)

            # --- Post-wait dependency check (amendment 2): recompute from actual state ---
            put_clean = self._put_task is None or self._put_task.done()
            workers_clean = all(t.done() for t in self._worker_tasks)
            all_deps_clean = (
                len(self._active_tasks) == 0 and read_clean and put_clean and workers_clean
            )

            # --- Phase 2: Registry cleanup (retained task, cached result) ---
            all_clean = all_deps_clean
            if all_deps_clean and hasattr(self.registry, "aclose"):
                if self._registry_close_task is None:
                    # Create once directly from registry.aclose(), store, and register completion observer (amendment 4)
                    self._registry_close_task = asyncio.create_task(self.registry.aclose())
                    self._registry_close_task.add_done_callback(self._capture_registry_result)

                # Observe existing task with registry-stage cap: min(0.5, remaining) (amendment 1)
                remaining = deadline - loop.time()
                registry_wait = min(0.5, remaining) if remaining > 0 else 0
                if registry_wait > 0:
                    try:
                        await asyncio.wait_for(
                            self._registry_close_done.wait(), timeout=registry_wait
                        )
                    except TimeoutError:
                        pass  # Still pending, return False

                # Retrieve cached result (no unbounded await)
                result = self._registry_close_result
                if result is True:
                    all_clean = True
                elif isinstance(result, BaseException):
                    all_clean = False
                    # If the cleanup itself was cancelled, that's a terminal failure
                    if isinstance(result, asyncio.CancelledError):
                        logger.debug("Registry cleanup was cancelled")
                else:
                    # Task still pending
                    all_clean = False

            # --- Finalize ---
            if all_clean:
                self._closed = True
                self._close_outcome = True
            else:
                self._closed = False
                self._close_outcome = False
            return all_clean
        except asyncio.CancelledError:
            # Caller cancelled: preserve task, return unresolved (amendment 3)
            raise
        except TimeoutError:
            return False
        finally:
            if lock_acquired:
                self._close_lock.release()

    async def handle_request(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        task = asyncio.current_task()
        if task is not None:
            self._active_tasks.add(task)
        try:
            return await self._handle_request_impl(msg)
        finally:
            if task is not None:
                self._active_tasks.discard(task)

    def _capture_registry_result(self, task: asyncio.Task[None]) -> None:
        """Capture registry cleanup result for retry observation (amendment 4)."""
        try:
            task.result()
            self._registry_close_result = True
        except asyncio.CancelledError:
            self._registry_close_result = asyncio.CancelledError("registry cleanup cancelled")
        except Exception as exc:  # noqa: BLE001
            self._registry_close_result = exc
        finally:
            self._registry_close_done.set()

    def _tool_definitions(self) -> list[dict[str, Any]]:
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
                        "operation": {
                            "type": "string",
                            "enum": ["read", "write", "create", "delete", "list", "mkdir"],
                        },
                        "path": {"type": "string", "description": "Path to file or directory"},
                        "content": {
                            "type": "string",
                            "description": "Content to write (required for write operation)",
                        },
                        "encoding": {
                            "type": "string",
                            "description": "Text encoding (default: utf-8)",
                        },
                    },
                    "required": ["operation", "path"],
                }
            elif name == "shell_execution":
                desc = "Execute sandboxed shell commands. Uses allowlist/denylist. No shell=True, direct binary execution."
                input_schema = {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Command to execute"},
                        "timeout": {
                            "type": "number",
                            "description": "Timeout in seconds (default: 30)",
                        },
                        "working_dir": {
                            "type": "string",
                            "description": "Working directory (default: current directory)",
                        },
                    },
                    "required": ["command"],
                }
            elif name == "code_analyzer":
                desc = "Analyze code files for bugs, security issues, performance problems, and style. Uses LLM for deep analysis when model provided."
                input_schema = {
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of file paths to analyze",
                        },
                        "focus": {
                            "type": "string",
                            "enum": ["security", "performance", "style", "general"],
                            "default": "general",
                        },
                        "context": {
                            "type": "string",
                            "description": "Additional context for the analysis",
                        },
                        "model": {
                            "type": "string",
                            "description": "Optional model ID for LLM-based analysis",
                        },
                        "models": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of model fallbacks",
                        },
                    },
                    "required": ["files"],
                }
            elif name == "web_search":
                desc = "Search the web for information. Supports Brave Search API and DuckDuckGo fallback."
                input_schema = {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query string"},
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "default": 10,
                        },
                        "provider": {
                            "type": "string",
                            "enum": ["brave", "duckduckgo"],
                            "description": "Search provider to use",
                        },
                        "timeout": {
                            "type": "number",
                            "description": "Request timeout in seconds (default: 30)",
                        },
                    },
                    "required": ["query"],
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

    async def _handle_request_impl(self, message: dict[str, Any]) -> dict[str, Any] | None:
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
                        "version": __version__,
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

    async def _read_bounded_line_custom(self, reader: asyncio.StreamReader) -> str | None:
        """Read a line bounded by max_frame_bytes from a custom asyncio StreamReader.

        Acquires data in small bounded chunks up to max_frame_bytes without buffering
        oversized payloads into memory. If the line exceeds max_frame_bytes, drains
        the remainder boundedly up to newline and emits an oversized frame error.
        """
        buf = bytearray()
        oversized = False

        while True:
            # Read bounded chunk of 1 byte or up to remaining capacity
            try:
                chunk = await reader.read(1)
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                return None

            if not chunk:
                # EOF reached
                if not buf and not oversized:
                    return None
                break

            if chunk == b"\n":
                break

            if not oversized:
                buf.extend(chunk)
                if len(buf) > self.max_frame_bytes:
                    oversized = True
                    buf.clear()
            # If oversized, chunk is discarded; continue draining until \n or EOF

        if oversized:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32600,
                    "message": f"Frame exceeds maximum limit of {self.max_frame_bytes} bytes",
                },
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()
            return ""

        return buf.decode("utf-8", errors="replace").strip()

    async def _read_bounded_line_stdio(self) -> str | None:
        """Read a line bounded by max_frame_bytes from stdin without unbounded buffering.

        Uses strictly byte-bounded character reads (readline(1)). If input exceeds
        max_frame_bytes before newline, drains remaining characters until newline
        without consuming past newline into the next frame.
        """

        def _read_from_stdin(cap: int) -> tuple[str | None, bool]:
            self._stdin_thread_active = True
            try:
                chars: list[str] = []
                cur_bytes = 0
                oversized = False

                while True:
                    # Read 1 character at a time so returned UTF-8 bytes are strictly bounded (<= 4 <= cap + 1).
                    # Constructor guarantees cap >= 3.
                    ch = sys.stdin.readline(1)
                    if not ch:
                        if not chars and not oversized:
                            return None, False
                        break

                    if ch == "\n":
                        break

                    if not oversized:
                        b_len = len(ch.encode("utf-8"))
                        if cur_bytes + b_len > cap:
                            oversized = True
                            chars.clear()
                        else:
                            chars.append(ch)
                            cur_bytes += b_len

                if oversized:
                    return "", True
                return "".join(chars).strip(), False
            finally:
                self._stdin_thread_active = False

        self._reading_stdin = True
        try:
            line_str, oversized = await asyncio.to_thread(_read_from_stdin, self.max_frame_bytes)
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            return None
        finally:
            self._reading_stdin = False

        if oversized:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32600,
                    "message": f"Frame exceeds maximum limit of {self.max_frame_bytes} bytes",
                },
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()
            return ""

        return line_str

    async def run(self, reader: asyncio.StreamReader | None = None) -> None:
        """Run the JSON-RPC message processing loop on stdio or custom reader."""
        self._run_task = asyncio.current_task()
        self._queue = asyncio.Queue(maxsize=self.max_queue_size)
        self._worker_tasks = []

        # Start bounded worker pool
        for i in range(self.max_concurrency):
            worker = asyncio.create_task(self._worker_loop(i), name=f"mcp-worker-{i}")
            self._worker_tasks.append(worker)

        # Background producer to decouple reading from queue saturation (R62)
        async def _reader_producer() -> None:
            assert self._queue is not None
            while not self._closing:
                if reader is not None:
                    self._read_task = asyncio.create_task(self._read_bounded_line_custom(reader))
                else:
                    self._read_task = asyncio.create_task(self._read_bounded_line_stdio())

                stop_t = asyncio.create_task(self._stop_event.wait())
                done, _ = await asyncio.wait(
                    {self._read_task, stop_t}, return_when=asyncio.FIRST_COMPLETED
                )

                if stop_t in done:
                    if not self._read_task.done():
                        self._read_task.cancel()
                    break

                stop_t.cancel()
                try:
                    line_str = self._read_task.result()
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    line_str = None

                if line_str is None:
                    # EOF reached on input
                    break

                if not line_str:
                    # Empty line or discarded oversized frame
                    continue

                fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
                try:
                    self._queue.put_nowait((line_str, fut))
                except asyncio.QueueFull:
                    # Bounded queue is full; reject surplus frame to ensure
                    # transport EOF is detected without deadlocking on saturated queue
                    req_id = None
                    is_notification = False
                    try:
                        msg = json.loads(line_str)
                        if isinstance(msg, dict):
                            req_id = msg.get("id")
                            if "id" not in msg and "method" in msg:
                                is_notification = True
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Non-fatal error parsing frame on queue full: %s", exc)

                    if not is_notification:
                        err_resp = {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32000, "message": "Queue saturated"},
                        }
                        sys.stdout.write(json.dumps(err_resp) + "\n")
                        sys.stdout.flush()

        producer_task = asyncio.create_task(_reader_producer(), name="mcp-reader-producer")

        try:
            # Wait for producer to finish (e.g. on EOF or stop) or stop_event
            stop_task = asyncio.create_task(self._stop_event.wait())
            done, _ = await asyncio.wait(
                {producer_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )

            if stop_task in done:
                producer_task.cancel()
            else:
                stop_task.cancel()
                # Clean EOF reached on input stream.
                # If queue is saturated or busy, give bounded grace for queued quick requests to complete
                # without indefinitely blocking EOF shutdown.
                if not self._closing and self._queue is not None:
                    try:
                        await asyncio.wait_for(self._queue.join(), timeout=0.1)
                    except (TimeoutError, asyncio.CancelledError):
                        pass
        except asyncio.CancelledError:
            pass
        finally:
            if not producer_task.done():
                producer_task.cancel()
                try:
                    await asyncio.shield(producer_task)
                except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                    logger.debug("Non-fatal error cancelling producer task: %s", exc)
            if not self._closing:
                await self.aclose()

    async def _worker_loop(self, worker_id: int) -> None:
        """Background worker that pulls requests sequentially from the bounded queue."""
        assert self._queue is not None
        while not self._closing:
            try:
                item = await self._queue.get()
            except asyncio.CancelledError:
                break

            if item is None:
                self._queue.task_done()
                break

            line_str, fut = item
            try:
                await self._process_line(line_str)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error processing line in worker %d: %s", worker_id, exc)
            finally:
                if not fut.done():
                    fut.set_result(None)
                self._queue.task_done()

    async def _process_line(self, line_str: str) -> None:
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
            return

        response = await self.handle_request(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


def run_mcp(registry: ToolRegistry | None = None) -> int:
    """Entry point for `agentcli mcp` command."""
    import signal

    server = MCPServer(registry=registry)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    run_fut: asyncio.Task[None] | None = None

    def _sig_handler() -> None:
        asyncio.create_task(server.aclose())

    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _sig_handler)
            except (NotImplementedError, RuntimeError, ValueError):
                pass

    run_exit_code = 0
    try:
        run_fut = loop.create_task(server.run())
        loop.run_until_complete(run_fut)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as exc:  # noqa: BLE001
        logger.error("MCP server failed: %s", exc)
        run_exit_code = 1
    finally:
        clean = True
        try:
            clean = loop.run_until_complete(server.aclose())
        except Exception as exc:  # noqa: BLE001
            logger.debug("Non-fatal error in run_mcp aclose: %s", exc)
            clean = False
        loop.close()

    if not clean:
        return 1
    return run_exit_code
