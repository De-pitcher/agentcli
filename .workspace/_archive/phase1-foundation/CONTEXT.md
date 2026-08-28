# Phase 1 — Foundation

Status: COMPLETE. Final PR merged to main. Archived 2026-08-26.

## What this phase built

Single-model chat CLI with file context injection, config, and a production-ready foundation.

### Modules delivered

| Module | Purpose |
|---|---|
| `agentcli/cli.py` | Argument parser, REPL loop (multi-line input with `\` continuation) |
| `agentcli/config.py` | TOML config, `load_config()`, `init_config()`, platform paths |
| `agentcli/exit_codes.py` | `ExitCode` enum: SUCCESS=0, GENERAL_ERROR=1, CONFIG_ERROR=2, USER_INTERRUPT=3 |
| `agentcli/files.py` | `@file` reference expansion, 200KB cap, `FileReadError` |
| `agentcli/openrouter_client.py` | Async httpx client, SSE streaming, exponential backoff, 429/5xx retry |

### Hardening delivered (post-Phase-1 polish)

- Multi-line chat input (`\` continuation)
- Distinct process exit codes
- Empty/blank model response handling
- `mypy --strict`, `ruff`, `dependabot`, `pip-audit` in CI
- pre-commit hooks
- `python -m build` clean

## Key decisions made in this phase (binding)

1. Single runtime dependency: `httpx`
2. Async throughout via `asyncio`
3. TOML config via stdlib `tomllib` (Python 3.11+)
4. Exit codes as an `IntEnum` — not raw integers in call sites
5. File injection caps at 200KB per file

## Tests delivered

`tests/test_cli.py`, `tests/test_config.py`, `tests/test_files.py`, `tests/test_openrouter_client.py`

## Coverage at close: ~94%
