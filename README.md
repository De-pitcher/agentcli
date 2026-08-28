# agentcli

A budget-conscious, model-agnostic AI agent CLI. Talks to any model available
through [OpenRouter](https://openrouter.ai), with a bias toward free-tier
models, and is designed to run comfortably alongside other CLI agents
(Codex, Aider, OpenCode, Antigravity, etc.) on modest hardware.

This is **Phase 6** of a 7-phase roadmap: single-model chat, file context,
multi-model auto-routing with cross-category fallbacks, a modular sub-agent system,
an in-process Plan → Act → Reflect agent loop, conversation memory persistence with
bounded LRU context caching, and adaptive rate-limit handling on an open-source-ready foundation.

## Quickstart

```bash
pip install -e .
export OPENROUTER_API_KEY=sk-or-...
agentcli config init      # writes a default config file
agentcli chat
```

### Resume and Browse Conversations

Persisted sessions are stored locally and can be resumed across restarts, complete with token metrics:

```bash
agentcli sessions list                # list saved conversations with token totals
agentcli sessions show <session-id>   # view message history and exact token / cost usage
agentcli chat --resume <session-id>   # resume an existing conversation
agentcli sessions clear --yes         # clear local session history
```

Inside a chat session, reference a file with `@`:

```
you> explain this file @src/main.py
```

Any `@path/to/file` token in your message is expanded into that file's
contents (as a fenced code block) before being sent to the model. Unchanged files
are automatically cached in an LRU-bounded memory pool to eliminate redundant disk reads and token spend.
You can also preload files for the whole session:

```bash
agentcli chat --file src/main.py --file src/utils.py
```


### Automatic Model Routing & Fallback Chains

By default, `agentcli chat` classifies each message (code / reasoning /
chat) and routes it to the best available free model. If all models in a category
are cooling down, the router seamlessly falls back across compatible categories
(`code` → `reasoning` → `chat`) and across all healthy models in the registry before raising `NoAvailableModelError`.

Force a specific model with `--model` (skips routing entirely),
inspect token usage with `--verbose`, and see which model answered with `--show-model`:

```bash
agentcli chat --show-model            # auto-route, print served model
agentcli chat --verbose               # show turn-level prompt/completion token usage
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
enabled = true                 # persist chat sessions to local SQLite database
# db_path = ""                 # optional: custom path to SQLite database
retention_days = 30            # auto-prune sessions older than N days (0 to disable)
budget_ratio = 0.75            # fraction of context window dedicated to conversation history (0.1 - 1.0)
cache_enabled = true           # cache unchanged file context to save tokens and disk I/O
max_cache_entries = 256        # maximum file context items retained in LRU cache
max_cache_bytes = 10485760     # 10MB memory ceiling for formatted file cache
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

`agentcli` stores conversation sessions and messages **100% locally** using standard library SQLite wrapped in non-blocking async execution:
- **Windows**: `%LOCALAPPDATA%\agentcli\memory.db` (or `%APPDATA%\agentcli\memory.db`)
- **Linux/macOS**: `$XDG_DATA_HOME/agentcli/memory.db` (or `~/.local/share/agentcli/memory.db`)

Your history is never uploaded to any central server or telemetry endpoint (only the active turn messages are sent to OpenRouter to fulfill the query). To inspect or delete local data:
- Inspect: `agentcli sessions list` and `agentcli sessions show <session-id>`
- Delete all: `agentcli sessions clear --yes`
- Disable persistence entirely: set `[memory] enabled = false` in `agentcli.toml`.

## Design Notes

- **Async, Connection-Pooled Client.** One `httpx.AsyncClient` per session,
  reused across turns — no per-request connection setup cost.
- **Streaming by Default.** Responses print token-by-token via SSE.
- **Adaptive Rate-Limiting & Backoff.** 429 rate limits dynamically scale cooldown duration
  exponentially per model without penalizing healthy models across categories.
- **Cross-Category Fallback Chains.** Seamless degradation from primary task categories to
  secondary categories and general healthy models with explicit `NoAvailableModelError` protection.
- **LRU-Bounded File Context Cache.** O(1) in-memory cache bounded by entry and byte quotas with
  fast SHA-256/mtime change detection.
- **Non-Blocking SQLite Store.** Background database writes run off the event loop via `asyncio.to_thread`
  with thread-safe connection locks.
- **Zero Heavy Dependencies.** `httpx` is the only external runtime dependency — keeps
  cold-start time and install footprint minimal.

## Performance Benchmarks

Profiled under Phase 6 load conditions (`python scripts/profile_and_bench.py`):

- **Single-Turn Local Pipeline**: **0.33 ms / turn** (classification + fallback resolution + token budget trimming)
- **Bounded LRU ContextCache**: **0.96 ms / access** under mixed 2,000-read workload
- **Concurrent Sub-Agent Throughput**: 100 tasks + memory writes across 5 concurrent agents completed in **0.72s**
- **Peak Memory Footprint**: **< 1.0 MB** (0.76 MB peak traced memory), far below the 200MB budget.

## Development

```bash
pip install -e ".[dev]"
pre-commit install
pytest                  # runs with --cov (requires 85% coverage)
ruff check .
ruff format --check .
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
advanced optimization ✅ → ecosystem integration & release (upcoming). See `.workspace/` and
project milestones for detailed phase context.


## License

MIT — see [LICENSE](LICENSE).


