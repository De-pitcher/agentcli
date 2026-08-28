# agentcli — Binding Architecture Decisions

These decisions are locked across all phases. Deviating requires an explicit discussion and update here.

## Stack invariants

| Constraint | Decision | Reason |
|---|---|---|
| Runtime dependencies | `httpx` only | Cold-start time and install footprint; runs alongside other CLI agents |
| Python support | 3.11 – 3.14 | CI matrix covers all four; dev machine is 3.14.3 on Windows |
| Async | `asyncio` throughout | Streaming SSE requires it; `asyncio.run()` at the CLI boundary |
| API backend | OpenRouter (OpenAI-compatible) | Single endpoint, free-tier model catalogue |
| Config format | TOML (`agentcli.toml` or platform path) | `tomllib` is stdlib in 3.11+ |
| Type checking | `mypy --strict` | Enforced in CI; no `# type: ignore` without comment explaining why |

## Module map (as of Phase 6 complete)

```
agentcli/
├── __init__.py          __version__ = "0.1.0"
├── __main__.py          sys.exit(main())
├── cli.py               Argument parsing + terminal I/O; delegates to AgentSession & runs subcommands
├── config.py            Config dataclasses, load_config(), ConfigError, _parse_int/_parse_float
├── exit_codes.py        ExitCode enum: SUCCESS=0, GENERAL_ERROR=1, CONFIG_ERROR=2, USER_INTERRUPT=3
├── files.py             @file reference expansion, FileReadError, ContextCache integration
├── openrouter_client.py OpenRouterClient (async httpx), ChatMessage, OpenRouterError, RateLimitedError
├── session.py           AgentSession — owns history, routing, streaming, registry health, loop dispatch
├── agent/               Phase 4: In-process Plan → Act → Reflect agentic loop
│   ├── events.py        LoopEvent hierarchy (PlanEvent, StepStartEvent, StepResultEvent, etc.)
│   ├── loop.py          AgentLoop engine, is_agentic_task heuristic
│   ├── protocols.py     PlannerProtocol, ExecutorProtocol, ReflectorProtocol
│   ├── reflector.py     DefaultReflector (pure heuristic goal reflection)
│   └── registry.py      ToolRegistry (executor interface over sub-agents and tools)
├── memory/              Phase 5/6: Local persistence, token budgeting, LRU caching
│   ├── budget.py        estimate_tokens, trim_history_to_budget, dynamic context windowing
│   ├── cache.py         ContextCache (LRU bounded, sha256/mtime invalidation, path caching)
│   ├── context_pool.py  SharedContextPool (two-phase compaction, reference counted)
│   └── store.py         MemoryStore (SQLite WAL, thread-safe RLock, asyncio.to_thread non-blocking)
├── routing/             Phase 2/6: Multi-model auto-routing & adaptive rate limiting
│   ├── classifier.py    classify(text) → "code" | "reasoning" | "chat" (pure regex, zero I/O)
│   ├── registry.py      ModelRegistry, ModelRecord, _Health, adaptive backoff scaling
│   └── router.py        Router.decide(category), cross-category fallbacks, NoAvailableModelError
└── subagents/           Phase 3: In-process sub-agent coordination
    ├── base.py          SubAgent base class, SubAgentConfig, lifecycle hooks
    ├── bus.py           MessageBus (async event pub/sub with typed topics)
    ├── code_analyzer.py Specialized code inspection sub-agent
    ├── file_ops.py      File reading/writing/listing sub-agent
    ├── planner.py       Multi-step plan decomposition sub-agent
    ├── shell.py         Sandboxed subprocess execution sub-agent
    ├── spawner.py       SubAgentSpawner (concurrency limits, lifecycle management)
    └── web_search.py    External query/search placeholder sub-agent
```

## Key design decisions

### Fallback strategy (Phase 2/6, binding)
Client passes `models=[primary, ...fallbacks]` array to OpenRouter. OpenRouter handles the actual failover server-side. `AgentSession` tracks health locally via `registry.mark_success/failure()` with adaptive exponential cooldown per model. Cross-category fallbacks degrade gracefully across compatible categories before raising `NoAvailableModelError`.

### AgentSession is the execution unit (binding)
`cli.py` handles user I/O and command dispatch. All routing, history, classification, streaming, persistence, and health tracking live in `AgentSession`. Sub-agents are coordinated in-process via `SubAgentSpawner` and `ToolRegistry`.

### Token Budgeting & Dynamic Context Windowing (Phase 5/6, binding)
System message is always preserved at index 0. Remaining history is dynamically trimmed to fit within the active model's context window (`budget_ratio`), with `history_turns` acting as a turn count ceiling.

### Non-blocking Persistence & LRU Context Caching (Phase 5/6, binding)
All SQLite database queries and writes during live streaming and agent turns execute asynchronously via `asyncio.to_thread` with `threading.RLock()` synchronization. File context references are bounded in an in-memory LRU cache.

### ConfigError (binding)
Malformed config values raise `ConfigError` (not `ValueError`). `main()` catches it and exits with `ExitCode.CONFIG_ERROR`. Never let a config parse error produce a raw traceback to the user.

