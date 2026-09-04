feat(phase13): Native OpenRouter Function Calling with Legacy Fallback

### Dual-Engine Function Calling Implementation
- **OpenRouterClient Expansion**: Added `chat_completion` non-streaming endpoint with retry & exponential backoff support. Added `tools` and `tool_choice` parameters to both `chat_stream` and `chat_completion`.
- **OpenAI-Compliant Tool Schemas**: Created `agentcli/tools_schema.py` standardizing all tool schemas (`file_ops`, `shell_execution`, `code_analyzer`, `web_search`) for native function calling.
- **PlannerAgent Dual-Engine**: Attempts native function calling using model-provided tools; automatically falls back to legacy prompt-based JSON planning if the model does not support native tools or returns an error.
- **ChatMessage Enhancements**: Supported `tool_calls`, `tool_call_id`, and `name` attributes while preserving backward compatibility.

### Quality Gates
- **279 tests pass** (8 new tests for native tools & fallback).
- **88.37% coverage** (≥85% floor enforced).
