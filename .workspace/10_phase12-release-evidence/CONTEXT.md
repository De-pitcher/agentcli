# Phase 12 - Release Evidence & MVP Launch Gate

Status: COMPLETE. Depends on Phase 11.

## Outcome

Publish only after independently reproducible evidence shows that the documented MVP works.

## Scope

1. Define the MVP contract: supported platforms, one provider, exact default tools, safety boundary, and known exclusions.
2. Execute the release checklist on a clean Windows host and CI; archive logs and package hashes.
3. Align semantic version, changelog, release workflow, PyPI publication, Docker tags, and documentation.
4. Run a short beta with representative tasks, triage defects, and make a go/no-go decision.

## Acceptance evidence

- No stub or disconnected feature appears as released functionality.
- A fresh user can install, configure, chat, run a safe agent task, and connect MCP using the published instructions.
- Release owner signs an evidence-backed go/no-go checklist.
