# Phase 6 — Optimization & Performance

Status: ACTIVE — Phase 5 merged to main (PR #12). Ready for Phase 6 design gate.


## What this phase will address

1. **Startup time**: cold import of httpx + routing + session adds latency; lazy imports where safe
2. **Memory footprint**: profiling peak RSS during a 20-turn session
3. **Free-model catalogue freshness**: `_BUILTIN_MODELS` in registry.py needs periodic audit; consider a refresh mechanism
4. **Streaming latency**: investigate first-token time per model, update registry priorities accordingly
5. **CI speed**: matrix run time; consider caching improvements

## Acceptance criteria (draft)

- `python -c "import agentcli"` completes in < 100ms on CI hardware
- 20-turn session peak RSS < 50MB
- A `agentcli registry refresh` command or similar keeps the model list current
- All existing tests still pass; coverage ≥ 85%
