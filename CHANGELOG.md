# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added — Phase 4: Custom Agent Core (Plan → Act → Reflect)

- New `agentcli.agent` package — lightweight, plugin-style agentic loop built entirely in-process (no external Node.js/cross-runtime dependencies):
  - `AgentLoop`: Orchestrates the Plan → Act → Reflect cycle over any number of iterations up to a configurable `max_iterations` hard ceiling. Yields structured `LoopEvent` dataclasses for display; cancels all in-flight tasks cleanly on exit.
  - `ToolRegistry`: Uniform execution interface over Phase 3 sub-agents. Extensible in Phase 7 via `registry.register(name, factory)` without modifying the loop engine.
  - `DefaultReflector`: Pure (no I/O) heuristic reflection stage. Classifies results as `FINISH | RETRY | REPLAN | FAIL` using transient/hard failure keyword heuristics and optional per-step `goal_criterion` string matching.
  - `LoopEvent` hierarchy: `PlanEvent`, `StepStartEvent`, `StepResultEvent`, `ReflectEvent`, `FinishEvent`, `LoopErrorEvent` — displayed under existing `--verbose` flag, no new flag.
  - `LoopIterationLimitError`: Raised when the loop hits `max_iterations` without finishing.
  - `is_agentic_task(text)`: Conservative heuristic that detects multi-step intent (checks for sequential keywords like "then", "first,", "step 1", etc.). Simple single-turn chat never matches — zero added latency for the common case.
  - Protocol definitions (`PlannerProtocol`, `ExecutorProtocol`, `ReflectorProtocol`) for future swappable component injection.
- **`PlannerAgent` extended** (Phase 3 class — not forked): Each plan step dict now includes a `goal_criterion` key (empty by default; settable by callers so `DefaultReflector` can verify step-level success). Docstring documents the Phase 3 / Phase 4 relationship explicitly.
- **`session.py`**: Added `should_use_loop(text) -> bool` (config gate + heuristic) and `run_loop(goal) -> AsyncIterator[LoopEvent]` that wires `AgentLoop`, `ToolRegistry`, and `DefaultReflector` together. Simple chat path untouched.
- **`cli.py`**: `run_chat` now branches: if `session.should_use_loop(expanded)` → loop path with `_render_loop_event` output; else → existing single-turn streaming path unchanged.
- **`config.py`**: New `AgentLoopConfig` dataclass (`enabled`, `max_iterations`, `reflection_enabled`, `plan_model_override`, `reflect_model_override`) and `[agent_loop]` TOML section with `ConfigError` validation. Defaults to `enabled = false` so existing installations are unaffected.
- **Tests** (`tests/test_agent_loop.py`): 38 new tests — happy path, re-plan path, mid-loop model errors, iteration ceiling, integration with real `PlannerAgent`, `is_agentic_task` regression, config parsing, and session gating.

### Added — Phase 3: Sub-Agent System
- Multi-agent coordination framework (`agentcli.subagents`):
  - `SubAgent` base class with asynchronous lifecycle hooks (`on_start`, `on_complete`, `on_failure`, `on_idle`, `kill`) and timezone-aware timestamps.
  - `MessageBus`: In-memory async pub/sub bus supporting broadcast, targeted message routing, request-response pairing with timeout protection, and automatic handler cleanup.
  - `SubAgentPool` & `SubAgentSpawner`: Pool management with per-type concurrency limits, global concurrency enforcement via active pool registry, and idle timeout garbage collection.
  - `CodeAnalyzerAgent`: Static code analysis and security inspection agent reusing `@file` reference loading.
  - `FileOpsAgent`: Safe filesystem CRUD operations with strict directory containment and path traversal protection.
  - `ShellExecutionAgent`: Subprocess runner using direct `asyncio.create_subprocess_exec` binary execution (preventing shell injection), command allowlist/denylist validation, dangerous environment variable sanitization, and output byte bounding.
  - `PlannerAgent`: Heuristic task decomposition and planning with strict subtask validation and fallback against `available_agents`.
  - `WebSearchAgent`: Web search agent stub returning graceful unavailable responses.
  - Configuration support: `[subagents]` TOML section with `enabled`, `max_concurrent`, `idle_timeout_seconds`, `default_timeout_seconds`, `max_output_bytes`, and custom `[[subagents.models]]` definitions.

### Fixed (Post Phase 1-2 Audit)
- Refactored `cli.py` to extract execution, routing, and history management logic into a new `AgentSession` class in `session.py`, paving the way for Phase 3.
- Scoped CI's `pip-audit` check with `--local` to prevent false positive vulnerability alerts from pre-installed runner packages.
- Config parser now safely catches type coercion errors (e.g. malformed integers) and raises a clear `ConfigError` instead of an unhandled traceback.
- Corrected `--model` help text to reflect that forcing a model explicitly bypasses task-based routing entirely.
- Modernized `pyproject.toml` to use PEP 621 `license = "MIT"` string format instead of the deprecated setuptools table format.

### Added — Phase 2: Multi-Model Routing
- Task-based auto-routing: each chat message is classified (code / reasoning
  / chat) by a zero-I/O heuristic classifier and routed to the best
  available free model from a built-in, data-driven registry.
- Hybrid fallback: the ordered candidate list is sent as OpenRouter's
  `models` array so the server fails over across models/providers remotely;
  the client handles transport errors, chain exhaustion, and health marking.
- Per-session model health tracking: consecutive failures (configurable
  threshold) or an immediate 429 put a model into cooldown; the router skips
  cooling-down models and success resets the streak.
- `--show-model` flag (and `--verbose`) prints the model that actually
  served each reply, including a notice when server-side fallback routed
  away from the requested primary.
- `--model` continues to force a specific model and bypass routing entirely
  (regression-tested).
- `[routing]` config section: `enabled`, `max_fallbacks`, `cooldown_seconds`,
  `failure_threshold`, plus optional `[[routing.models]]` entries that
  extend or override the built-in registry. Phase 1 config files work
  unchanged.
- Client: `chat_stream` accepts a `models` list, raises `OpenRouterError`
  on mid-stream SSE error events (`finish_reason: "error"` / inline error
  objects) instead of silently truncating, and exposes `last_served_model`.

### Fixed
- Default model replaced: `meta-llama/llama-3.1-8b-instruct:free` was retired
  from OpenRouter's free tier and returned 404 on first message. The default
  is now `google/gemma-4-31b-it:free` (verified live against the models API).
- Ctrl+C no longer dumps a traceback when the interrupt surfaces during async
  cleanup: `run_chat` closes the HTTP client best-effort, and `main` maps any
  real SIGINT reaching `asyncio.run` to exit code 3 (USER_INTERRUPT).
- httpx per-request INFO log lines no longer pollute the chat REPL.

### Fixed (Post Phase 1-2 Audit)
- Fixed mid-stream SSE error handling: errors with `finish_reason: "error"` or inline `error` objects now raise `OpenRouterError` instead of silently truncating.
- Fixed 429 error message when using `models` array: now shows the first model in the array instead of `None`.
- Fixed `KeyboardInterrupt` handling: `requested_primary` is now determined before the try block, avoiding `UnboundLocalError` on interrupt during streaming.
- Fixed health tracking: failure streak now resets after cooldown expires, preventing stale streaks from triggering premature cooldowns.
- Added validation for custom model registry entries: invalid categories now raise `ConfigError` with a clear message.
- Config parser now logs a warning when `routing.models` entries are skipped due to missing `id` field.
- Router now logs a warning when no healthy models are available for a category.
- SSE parser now safely handles missing or empty `choices` arrays.
- Fixed type hints: `AgentSession.mark_failure` now explicitly accepts `rate_limited` parameter.
- Removed unused `RateLimitedError` import from `session.py`.

### Changed
- Project metadata and API attribution headers now point at the real repository
  (`De-pitcher/agentcli`) instead of `your-org` placeholders.
- Python 3.13 and 3.14 classifiers added; CI matrix extended to match.

### Fixed
- `agentcli config init` now reports that a config file already exists instead
  of printing "Wrote default config" when it left an existing file untouched.
- Client test suite now covers the 5xx and network-error retry paths, exhausted
  retries for both, the missing-API-key constructor error, normal stream
  completion without a `[DONE]` sentinel, and async context-manager cleanup.

## [0.1.0] - 2026-08-26

### Added - UX polish & exit code ergonomics
- Multi-line input support in the chat REPL: lines ending with a trailing `\` continue prompting on the next line until a line without a trailing `\` is entered.
- Distinct process exit codes (`0` for success/clean exit, `1` for general/unexpected error, `2` for configuration/missing-key/missing-file error, `3` for user interrupt).
- Explicit visible notification when a model returns an empty or whitespace-only response (`(model returned an empty response)`).

### Fixed - post-hardening verification pass
- `openrouter_client.py`: `__aexit__` now has fully typed parameters
  (`exc_type`, `exc_val`, `exc_tb`) instead of an untyped `*exc` - closes the
  one real source-level gap `mypy --disallow-untyped-defs` caught.
- `pyproject.toml` now has a `[tool.mypy]` section
  (`disallow_untyped_defs = true` for source, relaxed for `tests/`) so
  "mypy passes" is a meaningful claim rather than default-leniency passing.
- Added `.github/dependabot.yml` (pip + GitHub Actions, weekly).
- Added `pip-audit` to CI as a dependency-vulnerability check.
- `tests/__init__.py` added so mypy's per-module override can target the
  `tests` package cleanly.

### Added — Phase 1: Foundation
- Interactive chat REPL (`agentcli chat`) against any OpenRouter model.
- `@path/to/file` inline context injection, plus `--file` for session preload.
- TOML configuration with project-local, env-override, and platform-default
  resolution (`agentcli config init` / `agentcli config show`).
- Async, connection-pooled OpenRouter client with SSE streaming and
  retry/backoff on 429 and 5xx responses.
- Open-source scaffolding: MIT license, CONTRIBUTING guide, GitHub Actions
  CI (Ubuntu + Windows, Python 3.11/3.12), issue template.
- Startup-time benchmark script.

### Added - hardening pass
- `pytest-cov` with an 85% coverage floor enforced in CI; full test coverage
  for `cli.py` and `openrouter_client.py` (previously untested), including
  mocked-transport streaming/retry/failure tests.
- `mypy` in CI, `py.typed` marker for downstream type-checking support.
- `CODE_OF_CONDUCT.md`, `SECURITY.md`, `.pre-commit-config.yaml`.
- `--version` flag, `python -m agentcli` support, `--verbose`/DEBUG logging.
- Interrupted chat streams now preserve the partial reply (marked
  `[interrupted]`) instead of discarding it silently.
