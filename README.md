# agentcli

A budget-conscious, model-agnostic AI agent CLI. Talks to any model available
through [OpenRouter](https://openrouter.ai), with a bias toward free-tier
models, and is designed to run comfortably alongside other CLI agents
(Codex, Aider, OpenCode, Antigravity, etc.) on modest hardware.

This is **Phase 5** of a 7-phase roadmap: single-model chat, file context,
multi-model auto-routing, a modular sub-agent system, an in-process
Plan → Act → Reflect agent loop, and conversation memory persistence with
context caching on an open-source-ready foundation. Optimization and packaging
land in upcoming phases.

## Quickstart

```bash
pip install -e .
export OPENROUTER_API_KEY=sk-or-...
agentcli config init      # writes a default config file
agentcli chat
```

### Resume and Browse Conversations

Persisted sessions are stored locally and can be resumed across restarts:

```bash
agentcli sessions list                # list saved conversations
agentcli sessions show <session-id>   # view message history
agentcli chat --resume <session-id>   # resume an existing conversation
agentcli sessions clear --yes         # clear local session history
```

Inside a chat session, reference a file with `@`:

```
you> explain this file @src/main.py
```

Any `@path/to/file` token in your message is expanded into that file's
contents (as a fenced code block) before being sent to the model. Unchanged files
are automatically cached in memory to eliminate redundant disk reads and token spend.
You can also preload files for the whole session:

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

[memory]
enabled = true         # persist chat sessions to local SQLite database
# db_path = ""         # optional: custom path to SQLite database
retention_days = 30    # auto-prune sessions older than N days (0 to disable)
cache_enabled = true   # cache unchanged file context to save tokens and disk I/O
max_shared_context_bytes = 524288  # 512KB capacity for shared sub-agent context pool

# Optional: extend or override the built-in model registry.
# An id that matches a built-in replaces it; new ids are appended.
# [[routing.models]]
# id = "z-ai/glm-5.2:free"
# categories = ["code", "reasoning"]
# priority = 10
# context_window = 128000
```

Run `agentcli config show` to see the resolved configuration.

## Privacy & Local Storage

`agentcli` stores conversation sessions and messages **100% locally** using standard library SQLite:
- **Windows**: `%LOCALAPPDATA%\agentcli\memory.db` (or `%APPDATA%\agentcli\memory.db`)
- **Linux/macOS**: `$XDG_DATA_HOME/agentcli/memory.db` (or `~/.local/share/agentcli/memory.db`)

Your history is never uploaded to any central server or telemetry endpoint (only the active turn messages are sent to OpenRouter to fulfill the query). To inspect or delete local data:
- Inspect: `agentcli sessions list` and `agentcli sessions show <session-id>`
- Delete all: `agentcli sessions clear --yes`
- Disable persistence entirely: set `[memory] enabled = false` in `agentcli.toml`.

## Design notes

- **Async, connection-pooled client.** One `httpx.AsyncClient` per session,
  reused across turns — no per-request connection setup cost.
- **Streaming by default.** Responses print token-by-token via SSE.
- **Retries with backoff.** 429/5xx/network errors get exponential backoff
  up to `max_retries` before surfacing an error.
- **Dynamic Token Budget & Context Windowing.** Trims history to fit safely within
  each model's specific context window (from registry) with `history_turns` acting as
  a turn upper bound.
- **Zero Heavy Dependencies.** `httpx` is the only external runtime dependency — keeps
  cold-start time and install footprint minimal, using Python standard library `sqlite3`
  for persistence.

## Performance

Measured on this Phase 5 baseline:

- **Session Load Latency**: <1ms (~0.85ms average for 50-message history retrieval)
- **Context Caching**: ~35x faster turn processing for unchanged referenced files with 0 redundant disk I/O.
- **Idle Process Memory**: well under the 200MB budget set for the full 7-phase project.

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

This repo grows through 7 phases: foundation ✅ → multi-model routing ✅
→ sub-agent system ✅ → custom agent core ✅ → memory & context ✅ →
optimization (active) → ecosystem integration & release. See `.workspace/` and
project milestones for detailed phase context.


## License

MIT — see [LICENSE](LICENSE).

