# Phase 5 — Memory & Context Persistence

Status: ACTIVE — Phase 4 merged to main (PR #9). Ready for Phase 5 design gate.

## What this phase will build

Persistent memory so the agent retains context across sessions:

- Conversation history that survives restart
- A structured knowledge store (facts, decisions, references the agent can query)
- Scoped retrieval — load only what the current task needs, not everything

## Note on ICM alignment

This phase is where the ICM Knowledge Bundle form becomes directly relevant as an implementation pattern. The memory store may be structured as a plain-text ICM Knowledge Bundle: claims with frontmatter, linked, queryable by the agent. No vector database required for Phase 5 — markdown + YAML is the target.

## Open questions for design gate

- Where does the memory store live? (platform data dir, project-local, configurable?)
- Session identity: how does the agent know which prior session is relevant?
- Retrieval strategy: keyword scan, frontmatter filter, or embedding-based?
- Write policy: what gets written to memory — everything, or only what the user explicitly says to remember?
- Privacy: is memory opt-in or opt-out? How does a user wipe it?

## Acceptance criteria (draft)

- Agent recalls facts from a previous session when asked
- Memory store is plain text — human can open, read, and edit it
- Retrieval adds ≤ 2,000 tokens to context per turn (no unbounded loading)
- `agentcli config clear-memory` wipes the store
- All existing tests still pass; coverage ≥ 85%
- No new runtime dependencies (sqlite or plain files only)
