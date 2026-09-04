# Phase 11 - End-to-End Reliability, Observability & Packaging

Status: COMPLETE. Depends on Phase 10.

## Outcome

Make the agent reproducible, diagnosable, and installable on declared support targets.

## Scope

1. Add hermetic tests plus optional credential-gated OpenRouter/MCP smoke tests; fix test-environment assumptions such as `TERM=dumb` and writable temp paths.
2. Add structured run logs, correlation IDs, provider/tool timing, cancellation, bounded retries, and cleanup checks.
3. Validate fresh-install console script, Docker behavior, session persistence, config initialization, and all documented commands.
4. Validate the stated Python support matrix against dependencies and Windows/POSIX subprocess behavior.

## Acceptance evidence

- Required quality gates pass from a clean checkout and CI uses the same commands.
- Windows and Linux smoke suites exercise chat, agent mode, MCP, memory, and plugin load.
- Packaging documentation names only artifacts actually built and published.
