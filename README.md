# agentcli

A budget-conscious, model-agnostic AI agent CLI. Talks to any model available
through [OpenRouter](https://openrouter.ai), with a bias toward free-tier
models, and is designed to run comfortably alongside other CLI agents
(Codex, Aider, OpenCode, Antigravity, etc.) on modest hardware.

**agentcli v1.0.0 (Phase 7 Complete)** provides:
- Single-model & auto-routed chat with fallback chains
- Modular sub-agent system & in-process Plan → Act → Reflect agent loop
- Local SQLite conversation memory persistence with token & cost tracking
- Bounded LRU context caching and dynamic token budget reconciliation
- Model Context Protocol (MCP) JSON-RPC stdio server for host integration
- Custom tool plugins & workflow presets (`coding`, `chat`, `minimal`)
- Automatic project instruction loading via `AGENTS.md`

---

## ⚡ Quickstart

```bash
pip install agentcli
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

### Context & Project Instructions

Inside a chat session, reference a file with `@`:

```
you> explain this file @src/main.py
```

Any `@path/to/file` token in your message is expanded into that file's contents (as a fenced code block). Unchanged files are automatically cached in an LRU-bounded memory pool.

`agentcli` also automatically discovers and prepends `AGENTS.md` project instructions from the current or parent repository directories. Disable with `--no-agents-md` if desired:

```bash
agentcli chat --no-agents-md
```

### Presets & Plugins

Apply curated presets or extend with custom tool plugins:

```bash
# Apply a workflow preset (coding, chat, minimal)
agentcli --preset coding chat

# Load custom Python tool plugins
agentcli --plugin examples/custom_tool_plugin.py chat
```

---

## 🔌 Model Context Protocol (MCP) Server

`agentcli` can run as a zero-dependency **MCP JSON-RPC stdio server**, exposing built-in sub-agent tools (`file_ops`, `shell_execution`, `code_analyzer`, `web_search`) and custom plugins to external agent hosts like Claude Desktop, Antigravity, or Cursor:

```bash
agentcli mcp
agentcli --plugin path/to/tools.py mcp
```

See [docs/mcp.md](docs/mcp.md) for host setup configurations.

---

## ⚙️ Configuration

`agentcli config init` writes a TOML config to your platform's config directory (`~/.config/agentcli/config.toml` on Linux/macOS, `%APPDATA%\agentcli\config.toml` on Windows). A project-local `agentcli.toml` in the current directory takes precedence if present.

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
load_agents_md = true
plugins = []

[routing]
enabled = true
max_fallbacks = 2
cooldown_seconds = 300
failure_threshold = 3

[memory]
enabled = true                 # persist chat sessions to local SQLite database
retention_days = 30            # auto-prune sessions older than N days (0 to disable)
budget_ratio = 0.75            # fraction of context window dedicated to conversation history
cache_enabled = true           # cache unchanged file context
max_cache_entries = 256        # maximum file context items in LRU cache
max_cache_bytes = 10485760     # 10MB memory ceiling for formatted file cache
max_shared_context_bytes = 524288  # 512KB capacity for shared sub-agent context pool
```

Run `agentcli config show` to see the resolved configuration.

---

## 📊 Performance & Benchmarks

Profiled across realistic multi-agent workloads:

| Metric | Measured Value | Methodology |
| :--- | :--- | :--- |
| **Local Orchestration** | **0.23 ms / turn** | `classify` + routing fallback + budget trimming |
| **LRU ContextCache Access** | **0.87 ms / access** | 2,000 file reads (98% hit rate) |
| **Concurrent Sub-Agents** | **0.60 s** total | 100 tasks across 5 concurrent workers + SQLite writes |
| **Traced Python Heap** | **0.77 MB** peak | `tracemalloc.get_traced_memory()` |
| **Process RSS Memory** | **~28 MB** | OS Working Set (comfortably under 200 MB budget) |

See [docs/benchmarks.md](docs/benchmarks.md) for details.

---

## 📦 Packaging & Distribution

- **PyPI / Wheel**: `pip install agentcli` or `pipx install agentcli`
- **Docker**: `docker build -t agentcli .` & `docker compose run agentcli`
- **Standalone Binary**: `pyinstaller --onefile --name agentcli agentcli/__main__.py`

See [docs/packaging.md](docs/packaging.md) for full deployment instructions.

---

## 📚 Architecture Decision Records (ADRs)

Key architectural decisions are documented in [docs/adr/](docs/adr/):
- [ADR 0001: Pure Python Runtime without External Harness Dependencies](docs/adr/0001-no-deepseek-harness-runtime-dependency.md)
- [ADR 0002: In-Process Async Task Pool vs Multiprocessing for Sub-Agents](docs/adr/0002-async-pool-over-process-for-subagents.md)
- [ADR 0003: Dynamic Token Budgeting and History Turns Reconciliation](docs/adr/0003-history-turns-token-budget-reconciliation.md)
- [ADR 0004: Non-Blocking SQLite Store via asyncio.to_thread and Thread-Safe Locking](docs/adr/0004-sqlite-thread-safety-async-wrappers.md)

---

## 🗺️ Roadmap Status

- **Phase 1: Foundation & Single-Model Chat** ✅
- **Phase 2: Multi-Model Routing & Classification** ✅
- **Phase 3: Sub-Agent Architecture & Message Bus** ✅
- **Phase 4: In-Process Agent Loop (Plan → Act → Reflect)** ✅
- **Phase 5: Memory Persistence & Context Caching** ✅
- **Phase 6: Advanced Optimization & Hardening** ✅
- **Phase 7: Ecosystem Integration & Community Release** ✅

---

## 📄 License

MIT — see [LICENSE](LICENSE).
