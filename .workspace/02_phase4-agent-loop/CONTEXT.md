# Phase 4 — Custom Agent Core: Plan → Act → Reflect Loop

## Status: COMPLETED — Hardened & Merging PR #9

## Summary
Phase 4 implements the lightweight, in-process Plan → Act → Reflect agentic loop.

## PR
- Branch: `feat/phase4-agent-loop`
- Target: `main`
- PR: #9 (https://github.com/De-pitcher/agentcli/pull/9)

## Deliverables

### New module: `agentcli/agent/`
| File | Purpose |
|---|---|
| `__init__.py` | Package init with re-exports |
| `events.py` | `LoopEvent` hierarchy for CLI display |
| `loop.py` | `AgentLoop` — the Plan→Act→Reflect engine + `is_agentic_task()` heuristic |
| `protocols.py` | `PlannerProtocol`, `ExecutorProtocol`, `ReflectorProtocol` for future swaps |
| `reflector.py` | `DefaultReflector` — pure heuristic critique/decide logic |
| `registry.py` | `ToolRegistry` — wraps Phase 3 sub-agents behind a uniform `execute()` interface |

### Extended modules
| Module | Change |
|---|---|
| `agentcli/subagents/planner.py` | Added `goal_criterion` key to each plan step dict; extended docstring documenting Phase 3/4 relationship |
| `agentcli/config.py` | Added `AgentLoopConfig` dataclass + `[agent_loop]` TOML parsing with `ConfigError` guard |
| `agentcli/session.py` | Added `should_use_loop()` and `run_loop()` — simple chat path unchanged |
| `agentcli/cli.py` | Added loop branch in `run_chat` + `_render_loop_event` helper |

### Tests
- `tests/test_agent_loop.py`: 44 tests covering all paths, retry, router fallback, and heuristic edges

## Quality Gates (verbatim)
```
ruff check:    All checks passed! (0 errors)
ruff format:   52 files already formatted (0 drift)
mypy:          Success: no issues found in 39 source files (0 errors)
pytest --cov:  147 passed — Total coverage: 94.44%
```

## Architecture decisions
- `is_agentic_task()` uses conservative keyword heuristics + regex step detection — Phase 5 may replace with LLM classifier
- Loop is disabled by default (`enabled = false`) — zero risk for existing installations
- `ToolRegistry` is the Phase 7 extension point — future tools register via `registry.register(name, factory)`
- `PlannerAgent` is reused (not forked) — Phase 4 wraps it with act/reflect
- `AgentLoop` depends on Protocols (`protocols.py`) for swappable components

## Next phase
Phase 5: Memory & Context Persistence

