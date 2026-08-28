# Phase 4 — Custom Agent Core (Plan / Act / Reflect Loop)

Status: ACTIVE — Phase 3 merged to main (PR #7). Beginning Phase 4 design gate and architectural planning.

## What this phase will build

A custom plan→act→reflect loop that replaces the simple REPL turn with a structured agent cycle:

- **Plan**: given a user goal, produce a step list
- **Act**: execute each step, potentially using sub-agents (Phase 3)
- **Reflect**: evaluate the output, decide whether to retry or surface to the user

## Inputs

- Completed Phase 3 (sub-agent coordination must exist)
- `_shared/architecture.md` — `AgentSession` is still the execution unit
- `agentcli/agents/` (Phase 3 deliverable)

## Open questions for design gate

- Where does the loop live — inside `AgentSession`, or a new `AgentLoop` class?
- How does the user interrupt mid-loop (Ctrl+C handling with partial results)?
- Does the plan step use a separate model/category classification, or the same classifier?
- Reflect step: how many reflection iterations max before surfacing to user?
- How does the loop interact with the REPL — does the user still see intermediate steps?

## Acceptance criteria (draft)

- User can say "do X" and the loop runs without further prompting until done or stuck
- User can Ctrl+C at any step and get partial results
- Loop uses sub-agents (Phase 3) for parallel steps
- All existing tests still pass; coverage ≥ 85%
- No new runtime dependencies
