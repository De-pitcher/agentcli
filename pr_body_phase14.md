feat(phase14): TUI Overhaul via prompt_toolkit, persistent history, and status spinner

### Terminal UI Enhancements
- **prompt_toolkit Integration**: Upgraded the interactive chat prompt to use `PromptSession` from `prompt_toolkit`.
- **Persistent Session History**: User prompts are automatically saved across sessions to platform-specific data directories (`~/.agentcli/history` or `%APPDATA%/agentcli/history`).
- **Tab Auto-Completion**: Added `SlashAndFileCompleter` providing instant tab completions for slash commands (`/exit`, `/quit`, `/help`, `/clear`) and `@file` context imports.
- **Multiturn Fallback**: Seamless fallback for headless/scripted/piped input and trailing backslash (`\`) continuation.
- **Visual Status Spinners**: Added `ConsoleRenderer.status_spinner` context manager providing animated spinners during long-running tasks and agent loop steps.
- **CLI Flags**: Added `--plain` and `--no-color` flags to allow users to toggle rich formatting or interactive prompts off.

### Quality Gates
- **288 tests pass** (9 new tests covering `prompt_toolkit` completion, history, fallback, and spinners).
- **88.39% coverage** (≥85% floor enforced).
- `ruff` and `mypy` 100% clean with 0 warnings.
