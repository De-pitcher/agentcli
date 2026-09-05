# agentcli v2.0.0 Production Enhancements (Phase 18)

## Overview
Following the successful completion of Phases 13 through 17 (Native Function Calling, TUI Overhaul, Autonomous Workspace Context, Autonomous Goal Execution Loop `agentcli run`, and Budget-Aware Multi-Agent Orchestration), this phase elevates `agentcli` to a full `v2.0.0` major release by adding interactive runtime ergonomics, intelligent multi-turn reflection for compound goals, isolated workspace sandboxing via git worktrees, and release packaging.

---

## 1. Interactive In-Session Slash Commands

### Goal
Allow developers inside the interactive chat prompt (`agentcli chat`) to dynamically inspect, query, and modify session state without having to exit or restart the CLI.

### Specifications
- **`/help`**: Display available in-session slash commands and keyboard shortcuts.
- **`/budget [low|medium|high]`**:
  - Without arguments: print current active budget tier.
  - With argument: dynamically update the session's active budget tier and router model pool.
- **`/model [model_id|auto]`**:
  - Without arguments: print current forced model or auto-routing state.
  - With argument: switch forced model or revert to `"auto"`.
- **`/goal <description>`**:
  - Trigger an inline multi-turn `AgentLoop` execution directly from the chat session and print the resulting output.
- **`/cost` / `/tokens`**:
  - Print live session token consumption breakdown (prompt tokens, completion tokens, total) and estimated accumulated USD cost.
- **`/clear`**:
  - Clear the terminal screen using cross-platform terminal escape sequences.
- **`/reset`**:
  - Clear conversation memory and start a fresh session while retaining active configuration.
- **Auto-completion**:
  - Register all slash commands in `SlashAndFileCompleter` with descriptions and parameter hints.

---

## 2. LLM-Assisted Compound Goal Reflector

### Goal
Resolve the issue discovered during production testing where compound, multi-clause goals (e.g. *"Check if file X exists, if not create it with content Y and run tests"*) prematurely terminate after completing an initial exploratory step (such as `file_ops: list`).

### Specifications
- Introduce `LLMReflector` in `agentcli/agent/reflector.py`.
- **Logic**:
  - When all step results in an iteration succeed, invoke a fast reasoning/chat model with the prompt:
    - Original Goal.
    - Executed Steps & Output Summaries.
    - Question: *Is the user's overall goal completely satisfied (FINISH), or are further steps needed to accomplish the remaining parts of the goal (REPLAN)?*
  - If more work is needed, return `ReflectOutcome(decision=ReflectDecision.REPLAN, reason=...)` so `AgentLoop` seamlessly advances to the next iteration.
  - If satisfied, return `ReflectOutcome(decision=ReflectDecision.FINISH, reason=...)`.
- Graceful heuristic fallback (`DefaultReflector`) if offline or model is unreachable.

---

## 3. Git Branch & Worktree Tool Adapter

### Goal
Provide safety and isolation when autonomous agents execute speculative or destructive code modifications.

### Specifications
- Extend `agentcli/subagents/workspace.py` with:
  - `git_worktree_create`: Create an isolated git worktree / temporary branch for experimenting with changes.
  - `git_worktree_remove`: Clean up worktree after verification.
- Expose schemas in `agentcli/tools_schema.py` and register with `ToolRegistry`.

---

## 4. v2.0.0 Production Release Packaging & Documentation

### Goal
Deliver a polished open-source release with complete documentation, upgraded versioning, and distribution build artifacts.

### Specifications
- **Version Bump**: Bump version from `1.0.0` to `2.0.0` in `pyproject.toml` and `agentcli/__init__.py`.
- **User Documentation (`README.md`)**:
  - Document all commands (`agentcli chat`, `agentcli run`, `agentcli sessions`, `agentcli config`, `agentcli mcp`).
  - Document budget tiers (`--budget {low,medium,high}`, `--max-cost <USD>`), presets, and in-session slash commands.
- **Changelog (`CHANGELOG.md`)**:
  - Add comprehensive release notes under `## [2.0.0]`.
- **Quality Gates**:
  - Full test suite passing with test coverage floor ≥ 85%.
  - Hermetic build and package verification (`python -m build`).
