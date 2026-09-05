# Implementation Plan: Phase 23 – Production Architecture Documentation & Ecosystem Guides

## Overview
With **Phases 1 through 22** fully implemented, audited (21/21 vectors passed), and verified at 86.92% test coverage (366 passing tests), **Phase 23** provides the complete production-grade architecture documentation, user guides, API reference updates, and host execution profiles for the modern `agentcli` platform.

---

## 🎯 Scope of Phase 23

### 1. New Technical Guides & Architecture Docs in `docs/`
1. **`docs/subagents_and_swarm.md` (Phase 20)**:
   - Event-driven `MessageBus` architecture (<10ms message latency).
   - Peer-to-peer delegation protocol (`MessageType.PEER_DELEGATE` & `MessageType.PEER_DELEGATE_RESULT`).
   - Recursion depth limits and cycle detection guards.
   - Multi-agent consensus engine (`ConsensusEngine`, `ConsensusStrategy`: Majority, Supermajority, Unanimous, Weighted, Plurality) and multi-round peer debate mechanics.
2. **`docs/tui_dashboard.md` (Phase 21)**:
   - Multi-pane terminal layout (`prompt_toolkit` VSplit / HSplit).
   - Real-time conversation stream with syntax highlighting.
   - Live sub-agent execution tree and status indicators.
   - Token speedometer and USD budget progress gauge.
   - Modal inspection views (File diffs `Ctrl+O`, Session history `Ctrl+H`).
   - Keybindings reference table.
3. **`docs/watcher_tdd.md` (Phase 22)**:
   - Continuous file system monitoring engine (`FileWatcher`).
   - Debouncing, ignore rules (`.git`, `__pycache__`, `.pytest_cache`), and thermal cooldown policies.
   - Git worktree sandboxing (`WorktreeManager`) for safe, isolated test execution and repair verification.
   - Auto-apply vs patch preview workflows.
4. **`docs/production_readiness.md`**:
   - Host execution profile (`peregrine001` Windows 15W TDP constraints, single-worker `maxWorkers: 1` mandate).
   - Security fences: file system read-only sandboxing, path traversal denial, shell command denylist, dangerous environment variable sanitization.
   - SQLite concurrency and memory persistence lifecycle.
   - Full 21-vector stress audit results and quality gates.

### 2. API Reference & Contract Synchronization (`docs/api.md`)
- Update `docs/api.md` with:
  - Phase 19: `MCPServer`, `MCPClient`, `MCPClientManager`, `MCPToolAgent`.
  - Phase 20: `MessageBus`, `SubAgentSpawner`, `ConsensusEngine`, `AgentVote`, `ConsensusResult`.
  - Phase 21: `TUIApplication`, `TUIState`.
  - Phase 22: `FileWatcher`, `ContinuousTDDRunner`, `WorktreeManager`, `WatcherConfig`.

### 3. README & Table of Contents Synchronization (`README.md`)
- Update documentation index and cross-references.
- Add quick links to all architecture guides.

---

## 🛣️ Subsequent Roadmap (Phases 24 – 26)

- **Phase 24**: Semantic Vector Indexing & Codebase Knowledge Embeddings (Local-first / Remote hybrid for instant symbol and snippet retrieval across massive codebases).
- **Phase 25**: Multi-Repository Cross-Project Orchestration & Monorepo Mesh (Orchestrating interconnected projects across separate git roots with dependency-aware routing).
- **Phase 26**: Automated Benchmark Suite & Agent Efficacy Arena (Automated evaluation across SWE-bench / HumanEval style developer tasks with latency, cost, and accuracy scorecards).

---

## 🔒 Verification & Quality Gates for Phase 23
- All markdown links validated and syntax checked.
- Verify `pytest`, `ruff`, and `mypy` maintain 100% compliance.
