# agentcli

A budget-conscious, model-agnostic AI agent CLI. Talks to any model available
through [OpenRouter](https://openrouter.ai), with a bias toward free-tier
models, and is designed to run comfortably alongside other CLI agents
(Codex, Aider, OpenCode, Antigravity, etc.) on modest hardware.

This is **Phase 2** of a 7-phase roadmap: single-model chat + file context +
auto-routing, on an open-source-ready foundation. Sub-agent system, a custom
plan/act/reflect agent loop, memory, optimization, and packaging land in
later phases.

## Quickstart

```bash
pip install -e .
export OPENROUTER_API_KEY=sk-or-...
agentcli config init      # writes a default config file
agentcli chat
```

Inside a chat session, reference a file with `@`:

```
you> explain this file @src/main.py
```

Any `@path/to/file` token in your message is expanded into that file's
contents (as a fenced code block) before being sent to the model. You can
also preload files for the whole session:

```bash
agentcli chat --file src/main.py --file src/utils.py
```

### Automatic model routing

By default, `agentcli chat` classifies each message (code / reasoning /
chat) and routes it to the best available free model, sending an ordered
candidate list so OpenRouter fails over automatically on rate limits or
outages. Force a specific model with `--model` (skips routing entirely),
and see which model actually answered with `--show-model`:

```bash
agentcli chat --show-model            # auto-route, print served model
agentcli chat --model google/gemma-4-31b-it:free   # force one model
```

### Multi-line Input

End any line with a trailing backslash `\` to continue input across multiple lines:

```
you> Here is a multi-line snippet:\
... def hello():\
...     return "world"
```

## Configuration

`agentcli config init` writes a TOML config to your platform's config
directory (`~/.config/agentcli/config.toml` on Linux/macOS,
`%APPDATA%\agentcli\config.toml` on Windows). A project-local `agentcli.toml`
in the current directory takes precedence if present; `$AGENTCLI_CONFIG` can
point at an arbitrary path.

```toml
[openrouter]
api_key_env = "OPENROUTER_API_KEY"
default_model = "google/gemma-4-31b-it:free"
timeout_seconds = 30
max_retries = 3
base_url = "https://openrouter.ai/api/v1"

[app]
stream = true
history_turns = 20

[routing]
enabled = true
max_fallbacks = 2
cooldown_seconds = 300
failure_threshold = 3

# Optional: extend or override the built-in model registry.
# An id that matches a built-in replaces it; new ids are appended.
# [[routing.models]]
# id = "z-ai/glm-5.2:free"
# categories = ["code", "reasoning"]
# priority = 10
# context_window = 128000
```

Run `agentcli config show` to see the resolved configuration.

## Design notes

- **Async, connection-pooled client.** One `httpx.AsyncClient` per session,
  reused across turns — no per-request connection setup cost.
- **Streaming by default.** Responses print token-by-token via SSE.
- **Retries with backoff.** 429/5xx/network errors get exponential backoff
  up to `max_retries` before surfacing an error.
- **Bounded context.** File injection caps at 200KB per file; chat history
  is trimmed to `history_turns` pairs to control token spend on free-tier
  models with small context windows.
- **Minimal dependencies.** `httpx` is the only runtime dependency — keeps
  cold-start time and install footprint small, since this tool is meant to
  run alongside several other CLI agents at once.

## Performance

Measured on this Phase 2 baseline (see `scripts/bench_startup.py`):

- Cold-import + argument-parser build: ~600ms on Windows (the `asyncio` import chain dominates — a lazy-import optimization is planned for the optimization phase)
- Idle process memory: well under the 200MB budget set for the full
  7-phase project

Run the benchmark yourself:

```bash
python scripts/bench_startup.py
```

## Development

```bash
pip install -e ".[dev]"
pre-commit install
pytest                  # runs with --cov (requires 85% coverage)
ruff check .
mypy .
```

## Exit Codes

| Code | Meaning | Description |
| :--- | :--- | :--- |
| `0` | `SUCCESS` | Clean execution or normal `/exit` / `EOF` termination |
| `1` | `GENERAL_ERROR` | General or unexpected error |
| `2` | `CONFIG_ERROR` | Configuration error (e.g. missing API key, unreadable `--file`) |
| `3` | `USER_INTERRUPT` | User pressed Ctrl+C at the top-level prompt |

## Roadmap

This repo will grow through 7 phases: foundation ✅ → multi-model routing ✅
→ sub-agent system → custom agent core → memory & context → optimization →
ecosystem integration & release. See project issues/milestones for current
status.

## License

MIT — see [LICENSE](LICENSE).
