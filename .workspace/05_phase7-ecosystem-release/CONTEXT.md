# Phase 7 — Ecosystem Integration & Release

Status: ACTIVE — Phase 6 merged to main (PR #17). Ready for Phase 7 execution.

## What this phase addresses

### 1. Folded-In Audit Items from Phase 6
1. **Explicit SSE Stream Usage Opt-In**: Add `"stream_options": {"include_usage": true}` to streaming payloads in `openrouter_client.py` so OpenRouter returns accurate provider token usage metadata on every SSE stream.
2. **Cross-Category Fallback Transparency**: Expose `is_cross_category_fallback` and original category on `RoutingDecision` and surface degradation cleanly in `--show-model` / `--verbose`.
3. **Memory Benchmark Documentation**: Clarify `tracemalloc` Python-heap allocation (<1MB) vs true OS process RSS (~25-35MB) in `README.md`.

### 2. Core Phase 7 Capabilities
1. **Plugin / Tool-Call Interface**: Dynamic registration interface for external tools into `ToolRegistry` allowing user and third-party scripts to be executed within `AgentLoop`.
2. **MCP (Model Context Protocol) Server**: Expose `agentcli mcp` subcommand implementing standard MCP JSON-RPC over stdio for external AI agent integration (Antigravity, Claude Desktop, Cursor).
3. **PyPI Release Automation**: Version bump to `1.0.0`, GitHub Actions release pipeline on `git tag v*`, publishing signed sdist and wheel to PyPI.
4. **Distribution & Packaging**: Brew and Winget manifest templates for standalone binary/CLI installation.
5. **Documentation**: MkDocs documentation site covering configuration, architecture, tool plugins, MCP setup, and CLI reference.

## Acceptance Criteria

- `agentcli mcp` responds to MCP protocol initialize and tool list/call requests over stdio.
- `ToolRegistry` dynamically registers external Python callables and shell commands.
- `pip install agentcli` installs a fully functional `agentcli` binary.
- `agentcli --version` reports `1.0.0`.
- Release workflow successfully triggers on `v*` tags with automated artifact publishing.
- All quality gates pass (`pytest --cov >= 85%`, `ruff check .`, `ruff format --check .`, `mypy .`, `pip-audit --local`, `python -m build`).

