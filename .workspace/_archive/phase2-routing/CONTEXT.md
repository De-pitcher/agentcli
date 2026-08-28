# Phase 2 — Multi-Model Routing

Status: COMPLETE. PR #5 merged. Post-audit cleanup PR #6 merged. Archived 2026-08-27.

## What this phase built

Task-based auto-routing: each chat message is classified and routed to the best available free model, with automatic server-side failover.

### Modules delivered

| Module | Purpose |
|---|---|
| `agentcli/routing/classifier.py` | `classify(text)` → "code" \| "reasoning" \| "chat" — pure regex, zero I/O |
| `agentcli/routing/registry.py` | `ModelRegistry` with `_BUILTIN_MODELS`, `_Health` dataclass, `candidates()`, `mark_success/failure()` |
| `agentcli/routing/router.py` | `Router.decide(category)` → `RoutingDecision(primary, fallbacks, models[])` |
| `agentcli/session.py` | **`AgentSession`** — extracted in post-audit; owns history, routing, streaming, health |

### Post-audit cleanup (PR #6) also delivered

- `ConfigError` + safe `_parse_int`/`_parse_float` in `config.py`
- `pip-audit --local` scoping in CI
- PEP 621 `license = "MIT"` in `pyproject.toml`
- `--model` help text updated to say "bypassing automatic routing"

## Key decisions made in this phase (binding)

1. **Hybrid server/client fallback**: client sends `models=[primary, ...fallbacks]` array to OpenRouter; server handles actual failover. Local health tracking is for future routing intelligence only — never retry client-side.
2. **AgentSession is the execution boundary**: `cli.py` does terminal I/O only. No routing code in `cli.py`.
3. **Classifier is pure regex**: zero network calls, zero imports from outside stdlib. Never replace with a model call for classification.
4. **Registry uses in-memory health state**: session-scoped, intentionally not persisted. Persistence comes in Phase 5.

## Tests delivered

`tests/test_classifier.py`, `tests/test_registry.py`, `tests/test_router.py`, `tests/test_session.py`

## Coverage at close: 94.64%

## Builtin model catalogue at close (14 free models)

Stored in `agentcli/routing/registry.py::_BUILTIN_MODELS`. Models rotate on OpenRouter's free tier — the catalogue needs periodic audit. No automated health-check job exists yet (Phase 6 concern).
