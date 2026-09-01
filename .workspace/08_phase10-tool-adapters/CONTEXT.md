# Phase 10 - Production Tool Adapters & Policy Enforcement

Status: PLANNED. Depends on Phase 9.

## Outcome

Deliver a small, dependable MVP tool set whose behavior matches its names and whose permissions are explicit.

## Scope

1. Turn `code_analyzer` into an actual analysis adapter (provider invocation or rename it to `prompt_builder` until it is one).
2. Implement one configured web-search provider with credentials, timeouts, result normalization, citation URLs, and graceful unavailability-or remove it from defaults.
3. Add typed MCP schemas/descriptions for each tool; pass approved configuration and plugins into the agent-loop registry.
4. Establish capability policy: read-only default, workspace-only file access, command allowlist, approval hooks for mutations, plugin trust boundary.

## Acceptance evidence

- Every default MCP tool has an end-to-end happy-path and denial-path test.
- Tool results include sufficient structured evidence for final-answer grounding.
- Plugin failure and unsupported configuration fail closed and visibly.
