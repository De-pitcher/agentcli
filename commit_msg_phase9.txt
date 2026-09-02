feat(phase9): Provider-backed CodeAnalyzerAgent & ToolRegistry config wiring

### CodeAnalyzerAgent: Real LLM Analysis
- Now calls OpenRouter LLM when `model` provided in payload
- Falls back to prompt-only when no model (backward compatible)
- Proper error handling with fallback on LLM failures

### ToolRegistry: Config Wiring
- Accepts `tool_configs` and `config` parameters
- Passes `Config` to `CodeAnalyzerAgent` via `_set_config()`
- Agents receive per-type config via `tool_configs`

### Quality Gates
- 211 tests pass
- 87% coverage (≥85% floor)
- ruff: clean
- mypy: clean