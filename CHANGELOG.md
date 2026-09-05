# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [2.9.0] - 2026-09-05

### Added — Phase 28: UI/UX Visual Modernization, Terminal Aesthetics Overhaul & Frame Snapshot Testing
- **Unified Design Tokens & Color Palette (`agentcli.ui.theme`)**:
  - Semantic ANSI 256 / 24-bit truecolor design tokens for role badges, tool indicators, telemetry metrics, diff views, and alerts.
  - Box drawing utilities (`draw_box`) with automatic UTF-8 Unicode (`BOX_ROUNDED`, `BOX_SQUARE`, `BOX_DOUBLE`) and plain ASCII fallback for legacy/restricted terminals.
  - Component badge rendering (`render_badge`) and visual progress bar formatting (`render_progress_bar`).
- **Interactive TUI Modal Inspectors (`agentcli.ui.tui_app.TUIApplication`)**:
  - Step Diff Inspector modal (`Ctrl+O`) displaying unified diffs for touched workspace files.
  - Session Timeline Browser modal (`Ctrl+H`) for chronological conversation and step history review.
  - Seamless keyboard navigation (`Escape` to close modals, `Tab` focus cycling).
- **Rich Terminal Rendering Extensions (`agentcli.ui.render.ConsoleRenderer`)**:
  - `render_step_tree()`: Hierarchical visual execution trees for multi-agent plan monitoring with status badges (`[DONE]`, `[RUNNING]`, `[PENDING]`).
  - `render_diff_preview()`: Colored unified diff containers with line count summaries (`+N / -N`).
  - `render_telemetry_banner()`: Compact token, latency, and budget status banner.
- **Virtual Terminal Buffer & Frame Snapshot Testing (`agentcli.ui.snapshot`, `tests/test_ui_snapshots.py`)**:
  - `VirtualTerminalBuffer` supporting line truncation, ANSI strip assertions, and column overflow verification (preventing horizontal wrapping on 80/120 column terminals).
  - 9 automated frame layout and snapshot tests verifying UI component alignment and width constraints.

## [2.8.0] - 2026-09-05

### Added — Phase 27: Real-World Seam Audit & End-to-End Dataflow Hardening
- **Centralized Prompt Token Expansion (`agentcli.session.AgentSession` & `agentcli.agent.loop.AgentLoop`)**:
  - Unified `prepare_prompt()` method automatically expanding `@file`, `@repo:<name>/<path>`, and `@semantic:<name>:<query>` tokens across all conversation modes (TUI, REPL, Watcher, and Autonomous Goal Runner).
  - Graceful fallback hints when referenced repositories or symbols are not found without unhandled crashes.
- **Incremental Vector Index Synchronization (`agentcli.embeddings.index.VectorIndex.sync_file`)**:
  - Single-file incremental indexing automatically synchronizing modified code chunks and purging stale chunks upon file edit or deletion.
  - Automatic background vector index synchronization in continuous TDD watcher daemon (`agentcli.watcher.FileWatcher`).
- **Fault-Tolerant Consensus Quorums (`agentcli.subagents.consensus.ConsensusEngine`)**:
  - Partial quorum evaluation supporting degraded node tracking when individual subagents encounter HTTP 429 rate limits or network timeouts.
- **SQLite Handle & Concurrency Defense (`agentcli.memory.store`, `agentcli.embeddings.store`)**:
  - Strict handle cleanup and WAL checkpointing (`wal_checkpoint(TRUNCATE)`) to prevent Windows `WinError 32` file locks.
- **Workspace Tool Scoping (`agentcli.subagents.workspace.WorkspaceAgent`)**:
  - Strict target workspace directory scoping and path boundary validation.

## [2.7.0] - 2026-09-05

### Added — Phase 26: Automated Benchmark Suite & Agent Efficacy Arena (`agentcli bench` & `agentcli arena`)
- **Standardized Benchmark Task Definitions (`agentcli.arena.task`)**:
  - `TaskCategory` taxonomy (`code_gen`, `bug_fix`, `refactor`, `tool_use`, `multi_file`, `mesh_orchestration`).
  - `BenchmarkTask` schema with workspace fixtures, expected file regexes, verification test commands, injected test files, timeouts, iteration limits, and tags.
- **Task Repository & Suite Loader (`agentcli.arena.loader.TaskLoader`)**:
  - Built-in hermetic `core` benchmark suite (HumanEval-style coding, SWE-bench bugfixing, JSON aggregation tool use, modular class refactoring).
  - Custom YAML and JSON suite loading (`agentcli bench run --file custom.json`).
  - Multi-dimensional filtering by category, tags, task ID, and suite name.
- **Isolated Workspace Evaluation Engine (`agentcli.arena.evaluator.TaskEvaluator`)**:
  - Automated sandbox verification against expected files, regex patterns, and subprocess test suite runs.
  - Granular `TaskResult` metrics: binary success/failure, exit reasons, wall-clock latency, turn counts, tool invocation counts, and USD cost tracking.
- **Benchmark & Multi-Model Arena Runners (`agentcli.arena.runner`)**:
  - `BenchmarkRunner`: Temporary isolated directory sandbox execution with single-worker sequential execution on `peregrine001`.
  - `ArenaRunner`: Multi-model head-to-head evaluation matrix across benchmark suites.
- **Scorecard & Leaderboard Generators (`agentcli.arena.scorecard.ScorecardFormatter`)**:
  - Terminal ASCII summary tables with pass rates, avg latency, and total costs.
  - Full GitHub-flavored Markdown evaluation reports.
  - Comparative multi-model Arena leaderboards ranked by accuracy, latency, and cost.
  - Machine-readable JSON output for CI regression tracking.
- **CLI Subcommands (`agentcli bench` & `agentcli arena`)**:
  - `agentcli bench list`: List suites and tasks with categories and tags.
  - `agentcli bench run`: Execute tasks in isolated sandboxes and render scorecards.
  - `agentcli arena compare`: Run head-to-head comparison across multiple models.
- **Configuration Integration (`agentcli.config.BenchmarkConfig`)**:
  - Added `[benchmark]` TOML configuration support (`default_suite`, `default_timeout_seconds`, `output_dir`, `record_traces`).

## [2.6.0] - 2026-09-05

### Added — Phase 25: Multi-Repository Orchestration & Monorepo Mesh (`agentcli mesh`)
- **Multi-Root Workspace Registry (`agentcli.mesh.registry.WorkspaceRegistry`)**:
  - Auto-discovery of multi-technology sub-projects (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `.git`) up to configurable recursion depths.
  - Manifest dependency inspection (npm workspace/file links).
  - Explicit workspace configuration loading from `[mesh.workspaces]` and path resolution (`@repo:<name>/<path>`).
- **Inter-Project Dependency DAG & Topological Engine (`agentcli.mesh.graph.ProjectDependencyGraph`)**:
  - Directed Acyclic Graph tracking direct and transitive cross-project dependencies.
  - Topological build/execution ordering with cycle detection (`DependencyCycleError`).
  - Downstream change impact analysis (`get_impacted_workspaces`) to identify affected packages when shared libraries change.
  - Formatted ASCII dependency graph visualization (`render_ascii_tree`).
- **Cross-Repository Semantic Search (`agentcli.mesh.search.MultiRepoIndex`)**:
  - Unified vector search across multiple repositories with automatic workspace attribution tags (`[workspace]`).
  - Scoped semantic search queries restricted to individual target repositories (`--repo <name>`).
- **CLI Subcommand `agentcli mesh` (`agentcli.cli`)**:
  - `agentcli mesh list`: Table of discovered and configured project roots with dependencies and metadata tags.
  - `agentcli mesh graph`: Dependency DAG tree and topological build order.
  - `agentcli mesh search <query>`: Cross-repository semantic code search.
  - `agentcli mesh run <task>`: Sequential autonomous agent execution across workspaces in topological order.
- **Context Injection Tokens (`agentcli.files`)**:
  - `@repo:<name>/<path>`: Reads files directly from named workspace roots.
  - `@semantic:<name>:<query>`: Scoped semantic vector search injected into chat prompts.
- **Sub-Agent Workspace Targeting (`agentcli.subagents.workspace.WorkspaceAgent`)**:
  - Supports `target_workspace` parameter for scoped sub-agent tasks.

## [2.5.0] - 2026-09-05

### Added — Phase 24: Semantic Vector Search & Codebase Knowledge Embeddings (`agentcli search`)
- **Language-Aware AST & Section Code Chunking (`agentcli.embeddings.chunker`)**:
  - Python AST parser extracting functions, async functions, classes, and module docstrings with exact line ranges and SHA-256 content hashes.
  - Markdown header section parser chunking documents by `#` headings (`#`, `##`, `###`).
  - Generic sliding-window chunker with configurable lines and overlap for other languages (`.ts`, `.js`, `.rs`, `.go`, `.java`, `.cpp`, `.c`, `.rb`, `.php`, `.yaml`, `.json`).
- **Persistent SQLite Vector Store (`agentcli.embeddings.store.VectorStore`)**:
  - Thread-safe SQLite vector store with WAL mode, parameterized queries, and composite indices on `(chunk_id, sha256, model)`.
  - Full CRUD operations, batch saves, staleness pruning (`delete_file_chunks`), and index statistics.
- **Embedding Engine with Deterministic Fallbacks (`agentcli.embeddings.engine.EmbeddingEngine`)**:
  - Async OpenRouter `/embeddings` API client (`openai/text-embedding-3-small`, batching, retries).
  - Deterministic feature-hashing fallback embeddings for zero-network testing and offline development.
- **Cosine Similarity Search Engine (`agentcli.embeddings.index.VectorIndex`)**:
  - In-memory vector similarity ranking with configurable thresholds and top-k filtering.
  - Workspace indexing with automatic cache hit skipping on unchanged SHA-256 hashes.
- **CLI Subcommand `agentcli search` (`agentcli.cli`)**:
  - Added `search` subcommand supporting natural language queries, `--top-k`, `--threshold`, `--filter`, `--index`, and colorized syntax previews.
- **Context Injection Token `@semantic:<query>` (`agentcli.files`)**:
  - Dynamic token expansion in chat prompts injecting the most relevant code chunks into context.
- **Autonomous Sub-Agent Semantic Search (`agentcli.subagents.workspace.WorkspaceAgent`)**:
  - Added `operation="semantic_search"` tool capability for multi-agent autonomous research and grounding.

## [2.4.0] - 2026-09-05

### Added — Phase 22: Autonomous Project Watcher & Continuous TDD Loop (`agentcli watch`)
- **Asynchronous Debounced File Watcher (`agentcli.watcher.FileWatcher`)**:
  - Monitors project source and test files (`.py`, `.ts`, `.js`, etc.) while filtering ignored directories (`.git`, `.venv`, `__pycache__`, `.pytest_cache`, `.agentcli_worktrees`, `node_modules`, `dist`, `build`).
  - Configurable debounce intervals (`debounce_seconds = 1.5`) coalescing rapid editor saves into single test triggers.
- **Continuous TDD Engine (`agentcli.watcher.ContinuousTDDRunner`)**:
  - Runs configured test suites (`--test-cmd`, default `python -m pytest`) on startup and upon file modification.
  - Intercepts test failures, extracts diagnostic tracebacks, and coordinates autonomous repair attempts.
- **Isolated Git Worktree Repair (`agentcli.watcher.WorktreeManager`)**:
  - Dynamically creates temporary Git worktrees on dedicated branches (`agentcli-repair-*`), runs `AgentLoop` in isolated workspaces with full write capabilities, verifies fixes by re-executing tests in the worktree, extracts unified git diffs, and optionally applies verified patches back to the working tree (`--auto-apply`).
  - Ensures clean teardown and branch deletion after every repair attempt.
- **Budget & Hardware Guardrails (`peregrine001`)**:
  - Enforces thermal and rate-limit cooldown periods (`--cooldown`, default 5.0s) between test executions.
  - Cumulative cost limits (`--max-cost`) and budget tier routing (`--budget`).
- **CLI Subcommand `agentcli watch` (`agentcli.cli`)**:
  - Added `watch` subcommand with `--test-cmd`, `--debounce`, `--cooldown`, `--auto-apply`, `--max-cost`, `--budget`, `--model`, `--max-iterations`, `--paths`, and `--no-initial`.

## [2.3.0] - 2026-09-05

### Added — Phase 21: Full-Screen Interactive TUI Dashboard (`agentcli tui`)
- **Multi-Pane TUI Application (`agentcli.ui.tui_app`)**:
  - Implemented `TUIApplication` and `TUIState` using `prompt_toolkit` with full-screen layout:
    - **Main Stream Pane**: Real-time conversation stream with role styling, timestamps, and auto-scroll.
    - **Sub-Agent Tree & Events Pane**: Live visualization of active sub-agents, running tasks, and message bus logs.
    - **Telemetry & Gauge Pane**: Real-time token usage breakdown (prompt, completion, cached, total), session USD spend, and dynamic ASCII budget progress bar.
    - **Command & Prompt Bar**: Multiline input buffer with slash-command and @file autocomplete.
- **Interactive Modals & Keybindings**:
  - `Tab`: Cycle active pane focus between input, chat, sub-agents, and telemetry.
  - `Ctrl+O`: Toggle full-screen step diff inspector popup.
  - `Ctrl+H`: Toggle session history timeline browser popup.
  - `Escape`: Close all open modal dialogs.
  - `Ctrl+C` / `Ctrl+D`: Interrupt stream / exit application.
- **CLI Subcommand (`agentcli.cli`)**:
  - Added `agentcli tui` supporting `--budget`, `--max-cost`, `--model`, and `--allow-write`.
- **Async Turn Stepper (`agentcli.session`)**:
  - Added `AgentSession.step()` for async single-turn execution, streaming, and persistence.

## [2.2.0] - 2026-09-05

### Added — Phase 20: Multi-Agent Swarm & Peer Delegation
- **Recursive Peer Delegation Protocol (`agentcli.subagents.base`, `agentcli.subagents.bus`)**:
  - Implemented `SubAgent.delegate()` enabling sub-agents to asynchronously dispatch tasks directly to sibling sub-agents via `MessageBus.delegate_task()`.
  - Added bounded recursion (`depth`, `max_depth = 3`) and cycle detection (`delegation_path`) to `SubAgentTask` to eliminate runaway spawning loops.
- **Spawner Event Dispatch (`agentcli.subagents.spawner`)**:
  - Wired `PEER_DELEGATE` listener in `SubAgentSpawner` to execute peer requests through managed agent pools under single-worker concurrency guardrails.
- **Multi-Agent Consensus & Peer Debate Engine (`agentcli.subagents.consensus`)**:
  - Implemented `ConsensusEngine`, `AgentVote`, `ConsensusResult`, and `ConsensusStrategy` supporting `MAJORITY`, `SUPERMAJORITY`, `UNANIMOUS`, `WEIGHTED`, and `PLURALITY` voting.
  - Implemented multi-round `debate_and_converge()` allowing agents to review prior round arguments and converge on optimal decisions.

## [2.1.0] - 2026-09-05

### Added — Phase 19: MCP Client & Dynamic External Tool Integrations
- **Asynchronous JSON-RPC 2.0 MCP Client (`agentcli.mcp.client`)**:
  - Implemented `MCPClient` supporting stdio transport, lifecycle management, initialization handshake (`initialize`), dynamic tool discovery (`tools/list`), and execution (`tools/call`) with configurable timeouts and process teardown.
- **MCP Tool Adapter (`agentcli.mcp.adapter`)**:
  - Implemented `MCPToolAgent` sub-agent wrapper and `mcp_tool_to_openrouter_schema` for OpenAI/OpenRouter-compatible function calling schemas.
- **MCP Client Manager (`agentcli.mcp.manager`)**:
  - Implemented `MCPClientManager` managing multi-server lifecycle, registering external MCP tools into `ToolRegistry`, and providing function-calling schemas to `OpenRouterClient`.
- **Configuration Support (`agentcli.config`)**:
  - Added `[mcp_servers.<name>]` configuration parsing support.
- **Session & CLI Lifecycle Wiring (`agentcli.session`, `agentcli.cli`)**:
  - Wired MCP client initialization and cleanup into `AgentSession`, `run_chat`, and `run_goal`.

## [2.0.0] - 2026-09-05

### Added — Phase 18: v2.0.0 Production Release & Developer Ergonomics
- **In-Session Interactive Slash Commands (`agentcli.ui.prompt`, `agentcli.cli`)**:
  - Added `/help`, `/budget [tier]`, `/model [model|auto]`, `/goal <task>`, `/tokens`, `/cost`, `/clear`, `/reset`, `/exit`, `/quit`.
  - Added dynamic completion in `SlashAndFileCompleter`.
- **LLM-Assisted Compound Goal Reflector (`agentcli.agent.reflector`, `agentcli.agent.loop`)**:
  - Implemented `LLMReflector` providing intelligent multi-turn evaluation of compound goals with seamless continuation across iterations.
- **Git Branch & Isolated Worktree Tool Adapters (`agentcli.subagents.workspace`, `agentcli.tools_schema`)**:
  - Added `git_branch` and `git_worktree` operations for isolated agent experimentation.
- **Subparser Option Inheritance (`agentcli.cli`)**:
  - Global flags (`--plain`, `--no-color`, `--verbose`, `--preset`, `--plugin`) are inherited with `default=SUPPRESS` across all subparsers.

### Added — Phases 13–17: MVP to Production-Ready Autonomous CLI
- **Native OpenRouter Function Calling & Dual-Engine Fallback (Phase 13)**: Direct tool payloads with system prompt JSON fallback for simpler models.
- **Terminal UI Overhaul (Phase 14)**: Multiline editing, persistent history, syntax-aware cursor navigation via `prompt_toolkit` and `rich`.
- **Autonomous Workspace Context & Git Grounding (Phase 15)**: Automatic `git status`/`git diff` context injection and `search_codebase` tool.
- **Autonomous Goal Execution Loop `agentcli run` (Phase 16)**: Headless multi-turn Plan -> Act -> Reflect loop with `--allow-write` autonomy.
- **Budget-Aware Multi-Agent Orchestration & Cost Tracking (Phase 17)**: `--budget {low,medium,high}`, `--max-cost <USD>`, token pricing matrix, and live cost calculation.

## [1.0.0] - 2026-09-04

- **MVP Contract Definition & Release Audit (`.workspace/10_phase12-release-evidence/RELEASE_EVIDENCE.md`)**:
  - Defined explicit v1.0.0 contract: supported OS matrix (Windows 11 & Linux), Python version bounds (3.11–3.14), default OpenRouter provider, tool capability boundaries (`file_ops`, `shell_execution`, `code_analyzer`, `web_search`), and safety guarantees.
  - Published evidence-backed release audit with SHA-256 package checksums (`dist/agentcli-1.0.0-py3-none-any.whl`, `dist/agentcli-1.0.0.tar.gz`).
- **Release Verification Suite (`tests/test_release_evidence.py`)**:
  - Added launch gate test suite validating version alignment across `pyproject.toml`, `agentcli.__version__`, and `agentcli --version`.
  - Audited build artifacts, CLI command suite execution (`chat`, `mcp`, `config`, `sessions`), and hermetic quality gates.

### Added — Phase 11: End-to-End Reliability, Observability & Packaging

- **Structured Run Observability & Tracing (`agentcli.agent.events`, `agentcli.agent.loop`)**:
  - Added correlation identifier `run_id` across `LoopEvent`, `PlanEvent`, `StepStartEvent`, `StepResultEvent`, `ReflectEvent`, and `FinishEvent`.
  - Added execution timing `duration_seconds` for individual step completions and total loop duration.
  - Added structured diagnostic logging with run ID, step numbers, agent types, and elapsed execution timings.
  - Added explicit loop cancellation handler `AgentLoop.cancel()` for clean in-flight task termination.
- **Provider Latency & Retry Logging (`agentcli.openrouter_client`)**:
  - Latency tracking recorded in `client.last_latency_seconds`.
  - Structured logging for backoff retries and completed completion streams.
- **Cross-Platform Stdio & Subprocess Resilience (`agentcli.mcp`, `agentcli.cli`)**:
  - Implemented cross-platform async stdio line reading in `MCPServer.run` using `asyncio.to_thread(sys.stdin.readline)`, eliminating Windows IOCP handle errors on `ProactorEventLoop`.
  - Added `--local` flag to `agentcli config init` for project-directory configuration initialization.
- **Packaging, Hermetic & E2E Validation (`tests/test_e2e_packaging.py`)**:
  - Added comprehensive subprocess E2E test suite validating console script entry points (`agentcli --version`, `agentcli --help`, `agentcli config`, `agentcli sessions`, `agentcli mcp`).
  - Tested session persistence and lifecycle across process restarts.
  - Validated environment resilience with `TERM=dumb`, `NO_COLOR=1`, and offline execution.

### Added — Phase 6: Advanced Optimization

- **ContextCache LRU Bounding (`agentcli.memory.cache`)**:
  - Configurable `max_entries` and `max_bytes` capacity ceilings with LRU eviction to prevent unbounded memory growth on long sessions.
  - Cached path resolution helper (`_resolve_path_str`) reducing filesystem realpath syscall overhead by >57%.
  - `stats()` enriched with `cached_bytes`, `max_entries`, and `max_bytes` metrics.
- **Adaptive Rate-Limiting (`agentcli.routing.registry`)**:
  - Per-model exponential backoff scaling ($2^{\min(\text{rate\_limits}-1, 4)} \times \text{base\_cooldown}$, capped at 3600s) on repeated 429 status codes.
  - Independent model cooldown tracking: cooling models do not penalize healthy models across other categories.
  - Success streaks immediately reset backoff multipliers to base.
- **Cross-Category Fallback Chains & `NoAvailableModelError` (`agentcli.routing.router`)**:
  - Tiered cross-category fallbacks (`code` → `reasoning` → `chat`, `reasoning` → `code` → `chat`, `chat` → `reasoning` → `code`).
  - Global fallback to all healthy models across the registry before raising an explicit `NoAvailableModelError`.
- **Non-Blocking Async Database Store (`agentcli.memory.store`)**:
  - Fully asynchronous non-blocking wrappers (`acreate_session`, `aappend_message`, `aget_messages`, `aget_session_stats`, `alist_sessions`, etc.) via `asyncio.to_thread`.
  - Re-entrant thread-safe locking (`threading.RLock`) on SQLite connection ensuring concurrent worker safety without cursor collisions.
- **Token & Cost Tracking (`agentcli.openrouter_client`, `agentcli.session`, `agentcli.cli`)**:
  - SSE chunk parser extracts exact `usage` metadata from OpenRouter API responses.
  - `agentcli sessions show <id>` displays total, prompt, and completion token breakdowns and cost estimates.
  - `agentcli sessions list` surfaces a `TOKENS` column for quick usage inspection.
  - `agentcli chat --verbose` renders per-turn token usage diagnostics.
- **Configurable Memory Budgeting (`agentcli.config`)**:
  - Exposed `budget_ratio` ($0.1 \le \text{ratio} \le 1.0$), `max_cache_entries`, and `max_cache_bytes` in `[memory]` TOML section with `ConfigError` validation.
- **Profiling & Performance Benchmark Suite (`scripts/profile_and_bench.py`)**:
  - Automated benchmark measuring single-turn latency (0.33ms/turn), LRU cache access (0.96ms/access), concurrent 5-agent throughput (0.72s for 100 tasks + DB writes), and memory footprint (<1MB peak).

### Fixed — Phase 5 Audit Corrections


- **In-Use Context Preservation (`agentcli.memory.context_pool`)**:
  - Compaction never mutates or truncates actively referenced items (`ref_count > 0`). In-use context chunks are preserved intact even when the pool temporarily exceeds target capacity, eliminating risk of downstream sub-agents operating on truncated text.
- **Session Resumption Validation (`agentcli.session`, `agentcli.cli`)**:
  - `agentcli chat --resume <id>` now verifies whether the session ID exists in the database. Genuinely nonexistent IDs trigger an explicit message (`No session found with ID '<id>'. Starting a new session instead.`) and generate a fresh session ID, while real-but-empty sessions correctly load with 0 messages.

### Added — Phase 5: Memory & Context Persistence


- **Persistence Layer (`agentcli.memory.store`)**:
  - Zero-dependency local SQLite storage using Python standard library `sqlite3`.
  - Schema with `sessions` and `messages` tables, WAL mode, foreign key cascade deletion, and indexed queries.
  - Platform-aware default storage path (`%LOCALAPPDATA%\agentcli\memory.db` on Windows, `$XDG_DATA_HOME/agentcli/memory.db` on Linux/macOS).
  - Auto-pruning retention policy for old sessions (configurable via `retention_days`).
- **Context Invalidation Caching (`agentcli.memory.cache`)**:
  - `ContextCache` with mtime and SHA-256 content hash verification.
  - Integrated into `files.py` (`read_file_for_context` and `expand_file_references`) to eliminate redundant disk reads and token spend on unchanged `@file` references across multi-turn sessions (~35x speedup).
- **Dynamic Token Budget & Context Windowing (`agentcli.memory.budget`)**:
  - Lightweight character-to-token heuristic estimation (~3.8 chars/token).
  - Dynamic `trim_history_to_budget` that preserves system prompts and recent turns within the active model's context window (from `ModelRegistry`).
  - **Reconciliation Decision**: Token budgeting subsumes Phase 1's blunt turn-count trimming. `history_turns` from `[app]` config is preserved as an optional turn ceiling / upper bound, guaranteeing safety while eliminating competing trimming behavior.
- **Shared Context Pool Compaction (`agentcli.memory.context_pool`)**:
  - Async-safe bounded context store for concurrent sub-agents extending Phase 3's reference tracking.
  - Automatic two-phase compaction (evicting zero-ref items first, then summarizing oversized referenced items) when capacity exceeds `max_shared_context_bytes`.
- **CLI Commands (`agentcli sessions`)**:
  - `agentcli sessions list`: browse past conversation sessions with message counts and timestamps.
  - `agentcli sessions show <id>`: display full message history for a session.
  - `agentcli sessions clear [--yes]`: clear local conversation history.
  - `agentcli chat --resume <id>`: continue an existing session with loaded history.
- **Configuration & Privacy (`agentcli.config`)**:
  - `[memory]` TOML section (`enabled`, `db_path`, `retention_days`, `cache_enabled`, `max_shared_context_bytes`).
  - Privacy disclosure documented in `README.md` guaranteeing 100% local persistence.

### Added — Phase 4: Custom Agent Core (Plan → Act → Reflect)


- New `agentcli.agent` package — lightweight, plugin-style agentic loop built entirely in-process (no external Node.js/cross-runtime dependencies):
  - `AgentLoop`: Orchestrates the Plan → Act → Reflect cycle over any number of iterations up to a configurable `max_iterations` hard ceiling. Yields structured `LoopEvent` dataclasses for display; cancels all in-flight tasks cleanly on exit.
  - `ToolRegistry`: Uniform execution interface over Phase 3 sub-agents. Extensible in Phase 7 via `registry.register(name, factory)` without modifying the loop engine.
  - `DefaultReflector`: Pure (no I/O) heuristic reflection stage. Classifies results as `FINISH | RETRY | REPLAN | FAIL` using transient/hard failure keyword heuristics and optional per-step `goal_criterion` string matching.
  - `LoopEvent` hierarchy: `PlanEvent`, `StepStartEvent`, `StepResultEvent`, `ReflectEvent`, `FinishEvent`, `LoopErrorEvent` — displayed under existing `--verbose` flag, no new flag.
  - `LoopIterationLimitError`: Raised when the loop hits `max_iterations` without finishing.
  - `is_agentic_task(text)`: Conservative heuristic that detects multi-step intent (checks for sequential keywords like "then", "first,", "step 1", etc.). Simple single-turn chat never matches — zero added latency for the common case.
  - Protocol definitions (`PlannerProtocol`, `ExecutorProtocol`, `ReflectorProtocol`) for future swappable component injection.
- **`PlannerAgent` extended** (Phase 3 class — not forked): Each plan step dict now includes a `goal_criterion` key (empty by default; settable by callers so `DefaultReflector` can verify step-level success). Docstring documents the Phase 3 / Phase 4 relationship explicitly.
- **`session.py`**: Added `should_use_loop(text) -> bool` (config gate + heuristic) and `run_loop(goal) -> AsyncIterator[LoopEvent]` that wires `AgentLoop`, `ToolRegistry`, and `DefaultReflector` together. Simple chat path untouched.
- **`cli.py`**: `run_chat` now branches: if `session.should_use_loop(expanded)` → loop path with `_render_loop_event` output; else → existing single-turn streaming path unchanged.
- **`config.py`**: New `AgentLoopConfig` dataclass (`enabled`, `max_iterations`, `reflection_enabled`, `plan_model_override`, `reflect_model_override`) and `[agent_loop]` TOML section with `ConfigError` validation. Defaults to `enabled = false` so existing installations are unaffected.
- **Tests** (`tests/test_agent_loop.py`): 38 new tests — happy path, re-plan path, mid-loop model errors, iteration ceiling, integration with real `PlannerAgent`, `is_agentic_task` regression, config parsing, and session gating.

### Added — Phase 3: Sub-Agent System
- Multi-agent coordination framework (`agentcli.subagents`):
  - `SubAgent` base class with asynchronous lifecycle hooks (`on_start`, `on_complete`, `on_failure`, `on_idle`, `kill`) and timezone-aware timestamps.
  - `MessageBus`: In-memory async pub/sub bus supporting broadcast, targeted message routing, request-response pairing with timeout protection, and automatic handler cleanup.
  - `SubAgentPool` & `SubAgentSpawner`: Pool management with per-type concurrency limits, global concurrency enforcement via active pool registry, and idle timeout garbage collection.
  - `CodeAnalyzerAgent`: Static code analysis and security inspection agent reusing `@file` reference loading.
  - `FileOpsAgent`: Safe filesystem CRUD operations with strict directory containment and path traversal protection.
  - `ShellExecutionAgent`: Subprocess runner using direct `asyncio.create_subprocess_exec` binary execution (preventing shell injection), command allowlist/denylist validation, dangerous environment variable sanitization, and output byte bounding.
  - `PlannerAgent`: Heuristic task decomposition and planning with strict subtask validation and fallback against `available_agents`.
  - `WebSearchAgent`: Web search agent stub returning graceful unavailable responses.
  - Configuration support: `[subagents]` TOML section with `enabled`, `max_concurrent`, `idle_timeout_seconds`, `default_timeout_seconds`, `max_output_bytes`, and custom `[[subagents.models]]` definitions.

### Fixed (Post Phase 1-2 Audit)
- Refactored `cli.py` to extract execution, routing, and history management logic into a new `AgentSession` class in `session.py`, paving the way for Phase 3.
- Scoped CI's `pip-audit` check with `--local` to prevent false positive vulnerability alerts from pre-installed runner packages.
- Config parser now safely catches type coercion errors (e.g. malformed integers) and raises a clear `ConfigError` instead of an unhandled traceback.
- Corrected `--model` help text to reflect that forcing a model explicitly bypasses task-based routing entirely.
- Modernized `pyproject.toml` to use PEP 621 `license = "MIT"` string format instead of the deprecated setuptools table format.

### Added — Phase 2: Multi-Model Routing
- Task-based auto-routing: each chat message is classified (code / reasoning
  / chat) by a zero-I/O heuristic classifier and routed to the best
  available free model from a built-in, data-driven registry.
- Hybrid fallback: the ordered candidate list is sent as OpenRouter's
  `models` array so the server fails over across models/providers remotely;
  the client handles transport errors, chain exhaustion, and health marking.
- Per-session model health tracking: consecutive failures (configurable
  threshold) or an immediate 429 put a model into cooldown; the router skips
  cooling-down models and success resets the streak.
- `--show-model` flag (and `--verbose`) prints the model that actually
  served each reply, including a notice when server-side fallback routed
  away from the requested primary.
- `--model` continues to force a specific model and bypass routing entirely
  (regression-tested).
- `[routing]` config section: `enabled`, `max_fallbacks`, `cooldown_seconds`,
  `failure_threshold`, plus optional `[[routing.models]]` entries that
  extend or override the built-in registry. Phase 1 config files work
  unchanged.
- Client: `chat_stream` accepts a `models` list, raises `OpenRouterError`
  on mid-stream SSE error events (`finish_reason: "error"` / inline error
  objects) instead of silently truncating, and exposes `last_served_model`.

### Fixed
- Default model replaced: `meta-llama/llama-3.1-8b-instruct:free` was retired
  from OpenRouter's free tier and returned 404 on first message. The default
  is now `google/gemma-4-31b-it:free` (verified live against the models API).
- Ctrl+C no longer dumps a traceback when the interrupt surfaces during async
  cleanup: `run_chat` closes the HTTP client best-effort, and `main` maps any
  real SIGINT reaching `asyncio.run` to exit code 3 (USER_INTERRUPT).
- httpx per-request INFO log lines no longer pollute the chat REPL.

### Fixed (Post Phase 1-2 Audit)
- Fixed mid-stream SSE error handling: errors with `finish_reason: "error"` or inline `error` objects now raise `OpenRouterError` instead of silently truncating.
- Fixed 429 error message when using `models` array: now shows the first model in the array instead of `None`.
- Fixed `KeyboardInterrupt` handling: `requested_primary` is now determined before the try block, avoiding `UnboundLocalError` on interrupt during streaming.
- Fixed health tracking: failure streak now resets after cooldown expires, preventing stale streaks from triggering premature cooldowns.
- Added validation for custom model registry entries: invalid categories now raise `ConfigError` with a clear message.
- Config parser now logs a warning when `routing.models` entries are skipped due to missing `id` field.
- Router now logs a warning when no healthy models are available for a category.
- SSE parser now safely handles missing or empty `choices` arrays.
- Fixed type hints: `AgentSession.mark_failure` now explicitly accepts `rate_limited` parameter.
- Removed unused `RateLimitedError` import from `session.py`.

### Changed
- Project metadata and API attribution headers now point at the real repository
  (`De-pitcher/agentcli`) instead of `your-org` placeholders.
- Python 3.13 and 3.14 classifiers added; CI matrix extended to match.

### Fixed
- `agentcli config init` now reports that a config file already exists instead
  of printing "Wrote default config" when it left an existing file untouched.
- Client test suite now covers the 5xx and network-error retry paths, exhausted
  retries for both, the missing-API-key constructor error, normal stream
  completion without a `[DONE]` sentinel, and async context-manager cleanup.

## [0.1.0] - 2026-08-26

### Added - UX polish & exit code ergonomics
- Multi-line input support in the chat REPL: lines ending with a trailing `\` continue prompting on the next line until a line without a trailing `\` is entered.
- Distinct process exit codes (`0` for success/clean exit, `1` for general/unexpected error, `2` for configuration/missing-key/missing-file error, `3` for user interrupt).
- Explicit visible notification when a model returns an empty or whitespace-only response (`(model returned an empty response)`).

### Fixed - post-hardening verification pass
- `openrouter_client.py`: `__aexit__` now has fully typed parameters
  (`exc_type`, `exc_val`, `exc_tb`) instead of an untyped `*exc` - closes the
  one real source-level gap `mypy --disallow-untyped-defs` caught.
- `pyproject.toml` now has a `[tool.mypy]` section
  (`disallow_untyped_defs = true` for source, relaxed for `tests/`) so
  "mypy passes" is a meaningful claim rather than default-leniency passing.
- Added `.github/dependabot.yml` (pip + GitHub Actions, weekly).
- Added `pip-audit` to CI as a dependency-vulnerability check.
- `tests/__init__.py` added so mypy's per-module override can target the
  `tests` package cleanly.

### Added — Phase 1: Foundation
- Interactive chat REPL (`agentcli chat`) against any OpenRouter model.
- `@path/to/file` inline context injection, plus `--file` for session preload.
- TOML configuration with project-local, env-override, and platform-default
  resolution (`agentcli config init` / `agentcli config show`).
- Async, connection-pooled OpenRouter client with SSE streaming and
  retry/backoff on 429 and 5xx responses.
- Open-source scaffolding: MIT license, CONTRIBUTING guide, GitHub Actions
  CI (Ubuntu + Windows, Python 3.11/3.12), issue template.
- Startup-time benchmark script.

### Added - hardening pass
- `pytest-cov` with an 85% coverage floor enforced in CI; full test coverage
  for `cli.py` and `openrouter_client.py` (previously untested), including
  mocked-transport streaming/retry/failure tests.
- `mypy` in CI, `py.typed` marker for downstream type-checking support.
- `CODE_OF_CONDUCT.md`, `SECURITY.md`, `.pre-commit-config.yaml`.
- `--version` flag, `python -m agentcli` support, `--verbose`/DEBUG logging.
- Interrupted chat streams now preserve the partial reply (marked
  `[interrupted]`) instead of discarding it silently.
