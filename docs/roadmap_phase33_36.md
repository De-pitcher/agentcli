# Implementation Plan: Phases 33 – 36 – Enterprise Extensibility, Interactive Review & Multi-Provider Architecture

## Overview
This roadmap establishes the next generation of enterprise agentic capabilities for `agentcli`. Building upon Phase 31 (Grep, Web Fetch, Checkpoints) and Phase 32 (Background Tasks, Clarification, Diagnostics), Phases 33–36 introduce custom skills/recipes, granular hunk-by-hunk patch review, persistent codebase knowledge graphs, and native multi-provider LLM gateways.

---

## 🧩 Phase 33: Custom Skills, Workflow Recipes & Dynamic Slash Commands (P0)

### 1. Repository & User Skill Manifests (`.agentcli/skills/`, `~/.agentcli/skills/`)
- **Module**: `agentcli/skills/loader.py` & `agentcli/skills/manifest.py`
- **Capabilities**:
  - Discover and load structured `SKILL.md` skill definitions from project workspace (`.agentcli/skills/<name>/SKILL.md`) and user home directory (`~/.agentcli/skills/<name>/SKILL.md`).
  - YAML frontmatter parser extracting metadata: `name`, `description`, `version`, `author`, `parameters`, `required_tools`, and `execution_mode` (chat, loop, subagent).
  - Jinja2/f-string template variable substitution injecting session context, active git branch, touched files, and user arguments.

### 2. Custom Workflow Recipe Engine & SubAgent Routing
- **Module**: `agentcli/skills/engine.py` & `agentcli/subagents/skill_runner.py`
- **Capabilities**:
  - Multi-step declarative workflow execution (e.g. `audit-security`, `generate-e2e-tests`, `refactor-module`, `pr-review`).
  - Step gating: execute step -> evaluate reflection criteria -> proceed or halt.
  - SubAgent isolation: runs skills in constrained sandboxes with configurable tool permission whitelists (`allow_write`, `allow_shell`, `network_allowed`).

### 3. Dynamic Slash Command Registration & Autocompletion
- **Module**: `agentcli/ui/prompt.py` & `agentcli/ui/tui_app.py`
- **Capabilities**:
  - Automatically registers loaded skills as first-class slash commands (e.g. `/audit`, `/refactor`, `/gen-tests`).
  - Interactive `/skill` management: `/skill list`, `/skill info <name>`, `/skill reload`, `/skill run <name> [args]`.
  - Dynamic dropdown completion in CLI prompt and Textual TUI dashboard.

---

## 🔍 Phase 34: Interactive Hunk-by-Hunk Patch Reviewer & Guided Diff Inspector (P1)

### 1. Granular Diff Parser & Hunk Splitter
- **Module**: `agentcli/diff/parser.py` & `agentcli/diff/hunk.py`
- **Capabilities**:
  - Split unified diffs into independent logical hunks with precise line range offsets (`@@ -start,len +start,len @@`).
  - Detect intra-hunk whitespace, syntax changes, and cross-file rename/deletion events.
  - Compute hunk collision and dependency graphs.

### 2. Interactive Terminal Hunk Reviewer & Approval Modal
- **Module**: `agentcli/ui/diff_reviewer.py` & `agentcli/ui/tui_app.py`
- **Capabilities**:
  - Visual color-coded hunk-by-hunk review in both interactive terminal chat and TUI modal.
  - Single-key actions per hunk:
    - `[y]` **Accept hunk**: Stage hunk for workspace application.
    - `[n]` **Reject hunk**: Drop hunk and record rejection rationale.
    - `[e]` **Edit hunk**: Open hunk in `$EDITOR` / internal editor for manual adjustment.
    - `[a]` **Accept all remaining**: Apply all pending hunks.
    - `[d]` **Discard all**: Reject entire turn changeset and trigger `/undo`.
    - `[?]` **Help**: Display keybinding guide.

### 3. Reflector Feedback Loop Integration
- **Module**: `agentcli/agent/loop.py` & `agentcli/agent/reflector.py`
- **Capabilities**:
  - Rejected hunks and user comments feed directly into the `LLMReflector` context.
  - The model autonomously revises rejected changes without redoing accepted modifications.

---

## 🧠 Phase 35: Persistent Codebase Knowledge Graph & Cross-Session Memory (P1)

### 1. SQLite Codebase Knowledge Graph (`.agentcli/knowledge.db`)
- **Module**: `agentcli/knowledge/graph.py` & `agentcli/knowledge/indexer.py`
- **Capabilities**:
  - Persistent relational index of codebase architecture: modules, classes, functions, imports, and call graphs.
  - Cross-file symbol resolution and dependency impact analysis (e.g. "what breaks if `AuthService.login()` signature changes?").
  - Background incremental updating triggered by `FileWatcher`.

### 2. Cross-Session Architectural Memory & Decision Vault
- **Module**: `agentcli/memory/knowledge_store.py`
- **Capabilities**:
  - Automatic extraction of codebase rules, architecture decisions, and bug-fix patterns into durable long-term memory.
  - Queryable memory recall across terminal restarts (`@rule`, `@arch`, `@decision`).
  - Explicit user memory controls: `/memory list`, `/memory add <fact>`, `/memory clear`.

### 3. Session Branching, Diffing & Merging
- **Module**: `agentcli/session_branching.py`
- **Capabilities**:
  - Branch conversation history: `/session branch <name>`, `/session switch <name>`.
  - Compare session branches: `/session diff <branch_a> <branch_b>`.
  - Export and import session transcripts as portable markdown bundles.

---

## 🔌 Phase 36: Multi-Provider LLM Gateway & Native Streaming Fallbacks (P2)

### 1. Native Provider Adapters (Direct SDK-Free HTTP/SSE)
- **Module**: `agentcli/providers/` (`anthropic.py`, `openai.py`, `gemini.py`, `ollama.py`, `openrouter.py`)
- **Capabilities**:
  - Direct HTTP/2 Server-Sent Events (SSE) streaming clients without bloated heavy SDKs.
  - Native support for **Anthropic** (Claude 3.5 Sonnet/Opus), **OpenAI** (GPT-4o/o3-mini), **Google Gemini** (Gemini 2.0 Flash/Pro), **DeepSeek**, and **Local Ollama/vLLM** (`localhost:11434`).

### 2. Real-Time Token-Level Interruption & Telemetry
- **Module**: `agentcli/providers/stream.py`
- **Capabilities**:
  - Clean `ESC` / `Ctrl+C` generation interruption without hanging sockets or corrupted memory state.
  - Live token generation speed (tokens/sec), time-to-first-token (TTFT), and latency profiling.

### 3. Automatic Cross-Provider Fallback Chaining
- **Module**: `agentcli/routing/gateway.py`
- **Capabilities**:
  - Tiered fallback cascades across providers: e.g. OpenRouter Primary -> Direct Anthropic Secondary -> Local Ollama Fallback.
  - Automatic error classification: HTTP 429 (rate limit), 503 (overloaded), 401 (auth failure), and connection timeouts.

---

## 🛡️ Core Constraints & Development Safeguards
1. **Single-Worker Laptop Profile (`peregrine001` 15W TDP)**: Strict `maxWorkers: 1`, non-parallel command execution, and bounded memory buffers.
2. **Windows & PowerShell First**: Strict backslash safety for paths in shell commands, forward slashes in code, and process tree termination (`taskkill /T /F`).
3. **Strict Quality Gates**: Zero `ruff` errors, zero `mypy` errors across all files, >= 85% test coverage, and green CI across Python 3.11–3.14 on Ubuntu and Windows.
