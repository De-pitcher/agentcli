# agentcli Public API Reference

Overview of the public Python APIs across `agentcli` packages.

---

## 1. Routing (`agentcli.routing`)

- **`classify(text: str) -> str`**: Pure regex heuristic classifying input into `"code"`, `"reasoning"`, or `"chat"`.
- **`ModelRegistry(config: RoutingConfig)`**: In-memory registry of models with adaptive exponential backoff cooldown per model.
  - `registry.candidates(category: str) -> list[ModelRecord]`
  - `registry.mark_success(model_id: str) -> None`
  - `registry.mark_failure(model_id: str, rate_limited: bool = False) -> None`
- **`Router(registry: ModelRegistry, max_fallbacks: int)`**:
  - `router.decide(category: str) -> RoutingDecision`
- **`RoutingDecision`**: Dataclass with `primary`, `fallbacks`, `is_fallback`, `requested_category`, and `served_category`.
- **`NoAvailableModelError`**: Raised when all registered models across all categories are cooling down.

---

## 2. Memory & Persistence (`agentcli.memory`)

- **`MemoryStore(db_path: Path | None = None)`**: Thread-safe SQLite persistence layer.
  - `async acreate_session(id, title, model, metadata) -> SessionRecord`
  - `async aappend_message(session_id, role, content, token_count) -> MessageRecord`
  - `async aget_messages(session_id, limit=None) -> list[MessageRecord]`
  - `async aget_session_stats(session_id) -> dict[str, Any]`
  - `async alist_sessions(limit=20) -> list[SessionRecord]`
- **`ContextCache(enabled=True, max_entries=256, max_bytes=10485760)`**: LRU-bounded file context cache with SHA-256 and mtime validation.
- **`trim_history_to_budget(history, max_context_tokens, max_turns, budget_ratio)`**: Trims chat history to dynamic token budget while preserving system prompts.

---

## 3. Agent Loop (`agentcli.agent`)

- **`AgentLoop(goal, registry, reflector, router, max_iterations=5)`**: Async Plan → Act → Reflect state engine yielding `LoopEvent` stream.
- **`ToolRegistry`**: Tool and sub-agent executor mapping.
  - `registry.register(agent_type, factory)`
  - `registry.register_callable(name, func, description)`
  - `registry.load_plugin_file(path)`
  - `async registry.execute(agent_type, payload) -> SubAgentResult`
- **`DefaultReflector`**: Heuristic goal reflection classifier (`FINISH`, `RETRY`, `REPLAN`, `FAIL`).

---

## 4. MCP Server (`agentcli.mcp`)

- **`MCPServer(registry=None)`**: Asynchronous JSON-RPC 2.0 stdio server.
  - `async handle_request(message: dict) -> dict | None`
  - `async run() -> None`
- **`run_mcp(registry=None) -> int`**: Synchronous runner for the `agentcli mcp` command.
