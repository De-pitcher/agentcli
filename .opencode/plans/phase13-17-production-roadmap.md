# agentcli MVP to Production-Ready CLI (Phases 13-17)

## Goal Description
Transform `agentcli` v1.0.0 from an MVP (basic tool execution, plain `input()` chat wrapper, system-prompt JSON hacking) into a robust, autonomous, production-ready developer CLI. The transition must maintain the core philosophy: budget-conscious, model-agnostic, and capable of scaling up its intelligence when run on decent hardware with higher-tier models.

This will be accomplished over **5 new logical phases** (Phases 13 through 17).

---

## Decisions Made
- **UI Upgrade**: `prompt_toolkit` dependency is approved.
- **Native Function Calling Support**: We will implement a **Dual-Engine Tool Calling System**. The client will inspect the model's capabilities (or try native tools first); if the model does not support native `tool_calls` via the OpenRouter API, the system will transparently fallback to injecting the tool schemas into the system prompt and parsing the text output as JSON. This guarantees 100% model-agnosticism.
- **Approvals & Autonomy (Antigravity Style)**: We will prioritize speed and autonomy. We will NOT implement tedious per-file `[y/N]` approval prompts. Instead, we will rely on the blanket `--allow-write` CLI flag to grant the agent full autonomy to execute file mutations rapidly.
- **Tool Streaming (Antigravity Style)**: We will stream conversational text instantly to the user for speed, but we will buffer tool call JSON chunks until the model finishes generating the complete tool call. This guarantees accuracy and prevents brittle mid-stream execution errors.

---

## Proposed Changes (Phases 13 - 17)

### Phase 13: Native OpenRouter Function Calling API (with Legacy Fallback)
**Goal**: Migrate to native LLM tool execution guarantees where supported, with a robust fallback for simpler/free models.

#### [MODIFY] `agentcli/openrouter_client.py`
- Extend the API payload inside `chat_stream` to optionally accept `tools` and `tool_choice`.
- Parse OpenRouter API response chunks for `tool_calls` delta objects.
- Implement a capability check or graceful error handling: if a model rejects the `tools` payload, transparently shift to legacy system-prompt injection for that model.

#### [MODIFY] `agentcli/subagents/planner.py`
- Remove the heavy system prompt instructing the model to return JSON text when native tools are supported.
- Pass the actual JSON schemas from `agentcli/mcp.py` directly into the `tools` parameter of the OpenRouter client.
- Handle tool execution via standard LLM multi-turn `tool_call` and `tool_result` message roles.

---

### Phase 14: Terminal UI (TUI) Overhaul (`prompt_toolkit`)
**Goal**: Provide a modern, highly responsive terminal interface without the extreme overhead of full-screen TUI frameworks. It must run lightning-fast on modest hardware.

#### [MODIFY] `pyproject.toml`
- Add `prompt_toolkit` to `dependencies`. Make `rich` a required dependency instead of optional.

#### [MODIFY] `agentcli/cli.py` & `agentcli/ui/render.py`
- Replace `input("you> ")` with `PromptSession.prompt()`.
- **Features gained natively**: 
  - True multi-line editing without needing `\` at the end of lines.
  - Up/Down arrow key command history (persisted locally).
  - Live cursor navigation.
- Add `rich.spinner` to indicate when background tool execution (e.g., shell commands) is running.

---

### Phase 15: Autonomous Workspace Context & Git Grounding
**Goal**: Allow the agent to "see" the project without the user manually typing `@filename` everywhere.

#### [NEW] `agentcli/subagents/workspace.py`
- Introduce a new subagent or tool adapter: `workspace_indexer`.
- Automatically executes `git status` and `git diff` in the background and injects the output silently into the context memory pool.
- Expose a `search_codebase` tool (using Python's `os.walk` + Regex, or a `ripgrep` subprocess fallback) so the agent can find files autonomously.

---

### Phase 16: Autonomous Goal Execution Loop (`agentcli run`)
**Goal**: Introduce a headless goal-execution mode for deep autonomous work.

#### [MODIFY] `agentcli/cli.py`
- Add a new command `agentcli run <goal_string>`.
- **Behavior**: Bypasses the chat REPL. Instead, it enters an uninterruptible `AgentLoop` that spans multiple turns of Plan -> Act -> Reflect.
- It will automatically execute shell commands and file mutations to solve the goal, halting only to request user input if a test fundamentally blocks it or if it reaches a configured iteration limit.

---

### Phase 17: Multi-Agent Orchestration & Budget Scaling (Budget-Aware)
**Goal**: Make the tool scale intelligently with hardware and budget. Use cheap/fast models for triage and expensive frontier models for deep execution.

#### [MODIFY] `agentcli/config.py` & `agentcli/routing/router.py`
- Introduce `budget_tier = "low" | "medium" | "high"` configuration.
- **Low Budget**: Forces all tasks (planning, code analysis, shell) onto a single fast model (e.g., `gemma-4-31b-it:free`).
- **High Budget**: Instructs the router to spawn parallel sub-agents (e.g., spinning up a Code Analyzer subagent using `claude-3.5-sonnet` concurrently with a Web Search agent) to solve complex tasks rapidly.
- Track precise token cost expenditures (mapping model token prices) in SQLite to hard-stop operations if a user-defined `--max-cost=$0.50` limit is exceeded.
