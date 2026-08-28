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

## Module map (as of Phase 2 + post-audit)

```
agentcli/
├── __init__.py          __version__ = "0.1.0"
├── __main__.py          sys.exit(main())
├── cli.py               Argument parsing + terminal I/O only; delegates to AgentSession
├── config.py            Config dataclasses, load_config(), ConfigError, _parse_int/_parse_float
├── exit_codes.py        ExitCode enum: SUCCESS=0, GENERAL_ERROR=1, CONFIG_ERROR=2, USER_INTERRUPT=3
├── files.py             @file reference expansion, FileReadError
├── openrouter_client.py OpenRouterClient (async httpx), ChatMessage, OpenRouterError, RateLimitedError
├── session.py           AgentSession — owns history, routing, streaming, registry health
└── routing/
    ├── classifier.py    classify(text) → "code" | "reasoning" | "chat"  (pure regex, zero I/O)
    ├── registry.py      ModelRegistry, ModelRecord, _Health, _BUILTIN_MODELS, candidates()
    └── router.py        Router.decide(category) → RoutingDecision(primary, fallbacks, models)
```

## Key design decisions

### Fallback strategy (Phase 2, binding)
Client passes `models=[primary, ...fallbacks]` array to OpenRouter. OpenRouter handles the actual failover server-side. `AgentSession` tracks health locally via `registry.mark_success/failure()` for future routing decisions. **Do not implement client-side fallback retry** — this defeats the server-side deduplication.

### AgentSession is the execution unit (post-audit, binding)
`cli.py` does terminal I/O only. All routing, history, classification, streaming, and health tracking live in `AgentSession`. Phase 3 sub-agents will be additional `AgentSession` instances; they must not reach into `cli.py`.

### History trimming (binding)
System message (if present) is always preserved at index 0. Remaining history is trimmed to `config.app.history_turns * 2 + 1` messages. This keeps token spend bounded on free-tier models.

### ConfigError (post-audit, binding)
Malformed config values raise `ConfigError` (not `ValueError`). `main()` catches it and exits with `ExitCode.CONFIG_ERROR`. Never let a config parse error produce a raw traceback to the user.

## What Phase 3 may add (pre-approved scope)

- New classes/modules for sub-agent coordination (e.g. `agentcli/agents/`)
- Extensions to `AgentSession` (e.g. spawning child sessions)
- New config keys under `[agents]` in `agentcli.toml`
- New `ExitCode` values if needed

## What Phase 3 must NOT do

- Add a second runtime dependency without explicit approval
- Modify `classifier.py`, `registry.py`, or `router.py` behavioural logic
- Bypass `AgentSession` and call `OpenRouterClient` directly from `cli.py`
- Break the 85% coverage floor or any existing test
