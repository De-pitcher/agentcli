# Phase 3 — Sub-Agent System

Status: COMPLETED & MERGED — PR #7 merged to main on 2026-08-28. All 101 tests pass, 95.48% coverage, 0 mypy/ruff errors. Ready for archive.

## Inputs (what this phase reads)

- `_shared/architecture.md` — binding constraints on what may/may not be added
- `_shared/quality-gates.md` — all gates must remain green
- `agentcli/session.py` — `AgentSession` is the execution unit; sub-agents coordinate specialized tasks
- `agentcli/routing/` — classifier/registry/router available for sub-agent routing

## Process (what happens in this phase)

Design and implement a sub-agent coordination layer that:
1. Allows `AgentSession` to spawn child `AgentSession` instances for parallel or sequential sub-tasks
2. Provides a mechanism for the parent to collect and merge sub-agent results
3. Keeps the single runtime dependency (`httpx`) — no new deps without explicit approval
4. Keeps `cli.py` clean — user sees sub-agent activity but `cli.py` does not orchestrate it

## Human gates (stop and check before proceeding)

1. **Design gate** — before writing code: agree on the sub-agent API surface (how a parent spawns a child, how results come back). Document in `references/design.md`. Wait for approval.
2. **Implementation gate** — after first working implementation: review `AgentSession` interface changes. Confirm no regression in existing tests.
3. **PR gate** — all 8 CI checks green before merge.

## Outputs (what this phase produces)

- `agentcli/agents/` or extensions to `agentcli/session.py`
- New tests in `tests/test_agents.py` or equivalent
- Updated `CHANGELOG.md` `[Unreleased]` section
- PR on `feat/phase3-sub-agents` merged to main
- This folder moved to `_archive/phase3-sub-agents/`

## Acceptance criteria

- Parent session can spawn ≥1 child sessions and collect results
- Child sessions use the same `Router` + `Registry` (or a scoped copy)
- All existing 69 tests still pass; coverage ≥ 85%
- No new runtime dependencies
- No routing, history, or classification logic in `cli.py`

## Open questions (resolve at design gate)

- Parallel vs sequential sub-agent execution — asyncio `gather` or sequential `await`?
- Shared vs independent registry health state per sub-agent?
- How does the user see sub-agent work — interleaved output or a summary at the end?
- Max sub-agent depth (flat only, or recursive spawning)?
- Error handling: if a sub-agent fails, does the parent continue or abort?
