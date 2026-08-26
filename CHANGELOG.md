# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

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
