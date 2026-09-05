# agentcli Next Horizon Roadmap (Phases 19 - 22)

## Overview
Following the successful completion and merging of **Phases 1 through 18 (v2.0.0 Production Release)**, this roadmap outlines the next major evolutions for `agentcli`. It expands the tool from a standalone coding CLI into an extensible agent hub capable of connecting to external Model Context Protocol (MCP) ecosystems, orchestrating multi-agent swarms, offering rich full-screen TUI observability, and running autonomous background continuous-improvement loops.

---

## Phase 19: MCP Client & Dynamic External Tool Integrations [COMPLETED ✅]

### 🎯 Goal
Enable `agentcli` to act not only as an MCP server, but as a full **MCP Client**, connecting to any external Model Context Protocol stdio or SSE server (e.g. GitHub, PostgreSQL, Brave Search, Puppeteer, filesystem servers) and dynamically making those tools available to `agentcli`'s planning and execution loops.

### 📋 Architectural Specifications
1. **MCP Client Core (`agentcli/mcp/client.py`)**:
   - Asynchronous JSON-RPC client supporting stdio and HTTP/SSE transports.
   - Lifecycle management (spawning server processes, performing protocol handshakes, and graceful shutdowns).
   - Tool discovery protocol: sends `tools/list` request on startup and parses returned schemas.
2. **Configuration (`agentcli.toml`)**:
   - New `[mcp_servers.<name>]` section supporting command, arguments, environment variables, and enabled/disabled flags:
     ```toml
     [mcp_servers.github]
     command = "npx"
     args = ["-y", "@modelcontextprotocol/server-github"]
     env = { GITHUB_PERSONAL_ACCESS_TOKEN = "ghp_..." }

     [mcp_servers.postgres]
     command = "npx"
     args = ["-y", "@modelcontextprotocol/server-postgres", "postgresql://user:pass@localhost:5432/db"]
     ```
3. **Dynamic Tool Adapter (`agentcli/mcp/adapter.py`)**:
   - Translates MCP tool schemas into `OpenRouter` tool definitions for native function calling and fallback prompts.
   - Wraps MCP tool invocations inside `SubAgentResult` and registers them into `ToolRegistry`.
4. **Safety & Permission Controls**:
   - Support whitelist/blacklist filtering for external tools.
   - Interactive prompt confirmation for external tools unless `--allow-write` / `--yes` is specified.

---

## Phase 20: Multi-Agent Swarm & Peer Delegation [COMPLETED ✅]

### 🎯 Goal
Elevate `agentcli`'s sub-agent architecture from a centralized hub-and-spoke loop (`AgentLoop` -> single sub-agent) to dynamic, recursive peer-to-peer delegation where specialized sub-agents can recruit sibling sub-agents to solve complex, multi-faceted problems.

### 📋 Architectural Specifications
1. **Peer Message Dispatch (`agentcli/subagents/bus.py`)**:
   - Extend `MessageBus` to allow sub-agents to post tasks to other agents and await structured responses asynchronously.
   - Enforce bounded recursion depth (default `max_depth = 3`) to eliminate infinite sub-agent spawning loops.
2. **Sub-Agent Delegation Protocol**:
   - `PlannerAgent` and `CodeAnalyzer` can spawn targeted `FileOps`, `ShellExecution`, or `WebSearch` sub-tasks directly during analysis.
   - Sub-agent concurrency control adhering strictly to single-worker/conservative laptop limits.
3. **Multi-Agent Consensus Pattern**:
   - Implement debate/voting pattern for ambiguous design choices or major refactors where multiple model perspectives are aggregated before executing code modifications.

---

## Phase 21: Full-Screen Interactive TUI Dashboard (`agentcli tui`) [COMPLETED ✅]

### 🎯 Goal
Provide a full-screen terminal user interface (TUI) for developers who prefer rich visual observability, split-pane navigation, and interactive diff inspection.

### 📋 Architectural Specifications
1. **TUI Application Shell (`agentcli/ui/tui_app.py`)**:
   - Built on `prompt_toolkit` application or modular terminal layout primitives.
   - Responsive multi-pane layout:
     - **Pane 1 (Main Stream)**: Real-time conversation stream with markdown rendering.
     - **Pane 2 (Sub-Agent Tree & Events)**: Live view of active sub-agents, tool executions, and status spinners.
     - **Pane 3 (Metrics & Speedometer)**: Real-time token usage gauge, cumulative USD spend, and budget progress bar.
2. **Interactive Controls & Keyboard Shortcuts**:
   - `Tab` / `Shift+Tab`: Switch pane focus.
   - `Ctrl+O`: Open full-screen step diff previewer.
   - `Ctrl+H`: Open interactive session history timeline browser.
   - `Ctrl+C` / `Ctrl+D`: Interrupt active generation / exit.

---

## Phase 22: Autonomous Project Watcher & Continuous TDD Loop (`agentcli watch`) [COMPLETED ✅]

### 🎯 Goal
Provide an autonomous continuous-testing and repair daemon that watches the codebase, detects test or lint failures immediately upon file changes, and autonomously generates verified fixes in an isolated worktree.

### 📋 Architectural Specifications
1. **File Watcher Daemon (`agentcli/watcher.py`)**:
   - Debounced filesystem event watcher targeting project source and test files (ignoring `.git`, virtual environments, and caches).
2. **Continuous TDD Agent Loop**:
   - Triggers fast test suites on save.
   - If tests fail, autonomously instantiates `AgentLoop` with the failure traceback as the goal.
   - Executes the fix in a temporary Git worktree (`git_worktree_create`).
   - Runs tests in the worktree. If green, prompts the developer or applies the verified patch back to the working tree.
3. **Budget & Thermal Throttling Guardrails**:
   - Enforce cooldown periods between automated runs.
   - Strict budget limit per watch session (`--max-cost`) to prevent unexpected LLM usage while editing.

---

## Verification & Quality Strategy
Across all phases (19-22):
- **Sequential Execution Guardrail**: Strict adherence to single-worker limits (`maxWorkers: 1`, sequential commands).
- **Hermetic Testing**: Mocked JSON-RPC stdio transports for MCP client tests without requiring internet or live npm binaries.
- **Coverage Floor**: Maintain test coverage strictly ≥ 85% across all new and modified modules.
- **Multi-Matrix CI**: All 10 GitHub Actions matrix jobs (Python 3.11–3.14 across Ubuntu and Windows, Docker, and Hermetic runs) must pass before merging.
