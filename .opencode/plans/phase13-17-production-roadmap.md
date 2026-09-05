# agentcli MVP to Production-Ready CLI (Phases 13-17) [COMPLETED]

## Goal Description
Transform `agentcli` v1.0.0 from an MVP (basic tool execution, plain `input()` chat wrapper, system-prompt JSON hacking) into a robust, autonomous, production-ready developer CLI. The transition maintained the core philosophy: budget-conscious, model-agnostic, and capable of scaling up its intelligence when run on decent hardware with higher-tier models.

All **5 phases** (Phases 13 through 17) have been implemented, verified via matrix CI, and merged into `main`.

---

## Decisions Made
- **UI Upgrade**: `prompt_toolkit` dependency integrated with rich rendering.
- **Native Function Calling Support**: Dual-Engine Tool Calling System (native OpenRouter `tools` payload with automatic fallback to system prompt injection for simpler models).
- **Approvals & Autonomy (Antigravity Style)**: Speed and autonomy prioritized with `--allow-write` CLI flag.
- **Tool Streaming (Antigravity Style)**: Conversational text streams live, tool JSON chunks buffered until turn completion.

---

## Completed Phases (Phases 13 - 17)

### Phase 13: Native OpenRouter Function Calling API (with Legacy Fallback) — [MERGED (PR #32)]
- Dual-engine function calling with tool schemas passed to OpenRouter `tools` parameter.
- Seamless fallback to prompt-injected JSON for models that do not support tool calling.
- Buffered streaming tool calls.

### Phase 14: Terminal UI (TUI) Overhaul (`prompt_toolkit`) — [MERGED (PR #33)]
- Replaced basic REPL with `prompt_toolkit.PromptSession`.
- Multi-line editing, persistent command history, syntax-aware cursor navigation.
- Rich spinner and interactive prompt rendering.

### Phase 15: Autonomous Workspace Context & Git Grounding — [MERGED (PR #34)]
- Implemented `workspace_indexer` and `search_codebase` tool.
- Auto-ingests `git status` and `git diff` into the memory context pool.

### Phase 16: Autonomous Goal Execution Loop (`agentcli run`) — [MERGED (PR #35)]
- Headless autonomous multi-turn `AgentLoop` via `agentcli run "<goal>"`.
- Plan -> Act -> Reflect loop with rich CLI live execution reporting.

### Phase 17: Multi-Agent Orchestration & Budget Scaling (Budget-Aware) — [MERGED (PR #36)]
- Multi-tier model routing (`low`, `medium`, `high`) with dynamic cross-tier fallback escalation.
- Token pricing matrix and exact cost tracking.
- Hard cost limit guardrails via `--max-cost <USD>`.
- CLI `--budget` and `--max-cost` flags for `chat` and `run`.

