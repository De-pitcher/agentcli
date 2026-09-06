# Implementation Plan: Phases 31 & 32 – Advanced Agentic Tooling, Fast Codebase Search & Safe Execution

## Overview
This roadmap introduces industry-standard, high-impact native tools and developer ergonomics to `agentcli`, closing capability gaps with state-of-the-art coding agents (e.g. Claude Code, Cursor, Aider, OpenCode, and Antigravity).

---

## 🎯 Phase 31: Core Research, Fast Codebase Discovery & Safe Rollback (P0)

### 1. Direct Web & Documentation Page Fetcher (`read_url_content` / `fetch_doc`)
- **Module**: `agentcli/subagents/web_fetch.py` & `agentcli/tools/web_fetch.py`
- **Capabilities**:
  - Direct HTTP(S) fetching of raw markdown, HTML-to-text, and API documentation with bounded byte offsets.
  - Automatic conversion from HTML to structured readable Markdown (stripping scripts, styles, and boilerplate nav).
  - GitHub PR/Issue/Diff reader with proper header normalization.
  - Safe host allowlisting, connection timeouts, and domain filtering.

### 2. High-Speed Workspace Regex & Pattern Grep (`grep_search` / `ripgrep`)
- **Module**: `agentcli/subagents/grep_search.py`
- **Capabilities**:
  - Fast workspace-wide regex and literal text search across non-ignored files.
  - Native binary search acceleration (uses `rg` / `ripgrep` if present on `PATH`, with a robust, zero-dependency streaming Python regex fallback).
  - Configurable include/exclude globs, case sensitivity, max matches cap (default 50), and formatted line numbers with surrounding context snippets.
  - Support for multi-language source, config (`.json`, `.yaml`, `.toml`, `.env`), and log files.

### 3. Checkpoint & Atomic Turn Rollback (`checkpoint_restore` / `/undo`)
- **Module**: `agentcli/agent/checkpoints.py` & UI slash command `/undo`
- **Capabilities**:
  - Automatic in-memory/git tree snapshot created before every user turn, plan step, or subagent invocation.
  - Instant `/undo` slash command in chat and TUI to revert filesystem changes made during the latest turn.
  - Diff preview before rollback (`/undo diff`).

---

## 🚀 Phase 32: Background Tasks, Diagnostics & Interactive Clarification (P1)

### 1. Background Process & Daemon Task Manager (`run_background` / `manage_task`)
- **Module**: `agentcli/subagents/task_manager.py` & `agentcli/agent/tasks.py`
- **Capabilities**:
  - Launch long-running commands (e.g. `npm run dev`, `pytest --watch`, Docker containers) asynchronously without blocking the main agent loop.
  - Lifecycle actions: `list`, `status`, `logs`, `send_input`, and `kill`.
  - Non-blocking notification hooks and automatic process termination upon session teardown.

### 2. Interactive User Clarification Tool (`ask_user_question` / `interactive_prompt`)
- **Module**: `agentcli/subagents/clarification.py`
- **Capabilities**:
  - Structured prompt modal allowing the model to request disambiguation or approval during complex multi-step tasks.
  - Single-choice, multi-choice, and open-ended text response options.
  - Clean non-blocking fallback for CI / non-interactive `--plain` executions.

### 3. Linter & Test Diagnostics Feedback Extractor (`diagnostics_check`)
- **Module**: `agentcli/tools/diagnostics.py`
- **Capabilities**:
  - Automatic parser for standard compiler and linter outputs (`ruff`, `mypy`, `pytest`, `tsc`, `eslint`).
  - Structured spans (`filepath`, `line`, `col`, `rule_id`, `message`) injected into model context for closed-loop error correction.

---

## 🔒 Architecture Principles & Quality Gates
1. **Single-Worker Laptop Safety (`peregrine001` 15W TDP)**: Enforce bounded memory and sequential execution across all new tools.
2. **Windows & PowerShell First**: Strict Windows path formatting, backslash safety, and subprocess process-group cleanup on process exit.
3. **100% Test Coverage & Quality Matrix**: Every tool accompanied by unit, integration, and hermetic mock suites passing `ruff`, `mypy`, and multi-version Python CI (3.11 - 3.14).
