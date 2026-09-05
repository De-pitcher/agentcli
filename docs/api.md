# agentcli Public API Reference

Overview of the public Python APIs across `agentcli` packages.

---

## 1. Routing (`agentcli.routing`)

- **`classify(text: str) -> str`**: Heuristic intent classifier mapping input into `"code"`, `"reasoning"`, or `"chat"`.
- **`ModelRegistry(config: RoutingConfig)`**: In-memory registry of models with adaptive exponential backoff cooldown and tier filtering (`low`, `medium`, `high`).
  - `registry.candidates(category: str, budget_tier: str | None = None) -> list[ModelRecord]`
  - `registry.healthy_models(budget_tier: str | None = None) -> list[ModelRecord]`
  - `registry.mark_success(model_id: str) -> None`
  - `registry.mark_failure(model_id: str, rate_limited: bool = False) -> None`
- **`Router(registry: ModelRegistry, max_fallbacks: int = 2, budget_tier: str = "low")`**:
  - `router.decide(category: str, budget_tier: str | None = None) -> RoutingDecision`
- **`RoutingDecision`**: Dataclass with `primary`, `fallbacks`, `is_fallback`, `requested_category`, `served_category`, and `budget_tier`.
- **`NoAvailableModelError`**: Raised when all candidate models across all categories and tiers are cooling down or rate-limited.

---

## 2. Memory & Persistence (`agentcli.memory`)

- **`MemoryStore(db_path: Path | None = None)`**: Thread-safe SQLite conversation and message persistence layer.
  - `create_session(session_id, title, model, metadata) -> SessionRecord`
  - `get_session(session_id) -> SessionRecord | None`
  - `append_message(session_id, role, content, token_count) -> MessageRecord`
  - `get_messages(session_id, limit=None) -> list[MessageRecord]`
  - `get_session_stats(session_id) -> dict[str, Any]`
  - `list_sessions(limit=50, offset=0) -> list[SessionRecord]`
  - `close() -> None`
  - Async equivalents: `acreate_session`, `aappend_message`, `aget_messages`, `aget_session_stats`, `alist_sessions`.
- **`ContextCache(enabled=True, max_entries=256, max_bytes=10485760)`**: LRU-bounded file context cache with SHA-256 and mtime validation.
  - `get_or_read(path, reader_fn) -> tuple[str, bool]`
  - `invalidate(path) -> bool`
  - `stats() -> dict[str, int]`
- **`estimate_tokens(text: str) -> int`**: Fast token estimation using character/word heuristics.

---

## 3. Agent Loop & Orchestration (`agentcli.agent`)

- **`AgentLoop(goal, registry, planner, reflector, router, max_iterations=5, max_cost_usd=None)`**: Plan → Act → Reflect state engine yielding `LoopEvent` stream.
  - `async run() -> AsyncIterator[LoopEvent]`
  - `cancel() -> None`
- **`ToolRegistry`**: Sub-agent and tool executor registry.
  - `register(agent_type, factory)`
  - `register_callable(name, func, description)`
  - `load_plugin_file(path)`
  - `async execute(agent_type, payload) -> SubAgentResult`
- **`DefaultReflector` / `LLMReflector`**: Evaluates step results against the goal (`FINISH`, `RETRY`, `REPLAN`, `FAIL`).

---

## 4. Model Context Protocol (MCP) (`agentcli.mcp`)

- **`MCPServer(registry=None)`**: Asynchronous JSON-RPC 2.0 stdio server for tool exposure.
  - `async handle_request(message: dict[str, Any]) -> dict[str, Any] | None`
  - `async run() -> None`
- **`MCPClient(config: MCPServerConfig)`**: Asynchronous client communicating with external MCP servers over stdio.
  - `async connect() -> None`
  - `async list_tools() -> list[dict[str, Any]]`
  - `async call_tool(name: str, arguments: dict[str, Any]) -> str`
  - `async close() -> None`
- **`MCPClientManager`**: Manages multiple named MCP server connections and aggregate tool discovery.
- **`MCPToolAgent(client, tool_name)`**: Adapts an external MCP tool as an internal sub-agent.
- **`mcp_tool_to_openrouter_schema(mcp_schema)`**: Bidirectional schema converter to OpenRouter function calling format.

---

## 5. Multi-Agent Swarm & Consensus (`agentcli.subagents`)

- **`MessageBus(handler_timeout: float = 5.0)`**: High-speed (<10ms) pub/sub event bus.
  - `subscribe(message_type, handler, target=None)`
  - `async publish(message: Message) -> None`
  - `async delegate_task(task: SubAgentTask, timeout: float = 30.0) -> SubAgentResult`
  - `async request_response(message, expected_type, timeout) -> Message | None`
- **`SubAgentSpawner(config, agent_factories, message_bus)`**: Manages pooled sub-agents and handles `PEER_DELEGATE` events with recursion depth checks.
- **`ConsensusEngine`**: Multi-agent voting and debate aggregation.
  - `evaluate_votes(votes: list[AgentVote], strategy: ConsensusStrategy) -> ConsensusResult`
  - `async run_debate(topic, participants, rounds=2) -> ConsensusResult`

---

## 6. Terminal UI & Interactive Dashboard (`agentcli.ui`)

- **`ConsoleRenderer(plain: bool = False, no_color: bool = False)`**: Terminal renderer with rich color support and automatic non-TTY fallbacks.
  - `render_markdown(text: str) -> None`
  - `render_file_preview(path: str, content: str) -> None`
  - `status_spinner(message: str) -> ContextManager`
- **`TUIApplication(config: Config, session: AgentSession | None = None)`**: Full-screen interactive dashboard.
  - `add_message(role: str, text: str)`
  - `add_subagent_event(agent_type: str, event_text: str)`
  - `update_telemetry(prompt_tokens, completion_tokens, cached_tokens, cost_usd)`
- **`TUIState`**: Dataclass modeling interactive dashboard telemetry, pane focus, and modal states.

---

## 7. Continuous Watcher & TDD Loop (`agentcli.watcher`)

- **`FileWatcher(paths, ignore_patterns=None, debounce_seconds=1.0)`**: Filesystem monitor pruning `.git`, `__pycache__`, `.pytest_cache`.
  - `scan() -> dict[Path, float]`
  - `detect_changes(baseline) -> tuple[set[Path], set[Path], set[Path]]`
- **`ContinuousTDDRunner(config: Config)`**: Test execution, failure extraction, and repair coordination.
  - `async run_test_suite(working_dir=None) -> TestExecutionResult`
  - `async attempt_repair(failure_summary, changed_files) -> RepairPatch | None`
- **`WorktreeManager(repo_root: Path | None = None)`**: Isolated git worktree lifecycle for sandboxed test verification.
  - `create_worktree(branch_name=None) -> Path`
  - `cleanup_worktree(worktree_path) -> None`
