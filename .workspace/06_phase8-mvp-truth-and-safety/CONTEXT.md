# Phase 8 - MVP Truth, Safety & Windows Baseline

Status: ACTIVE. Source: `docs/audits/agentcli-completion-audit-2026-08-31.md`.

## Outcome

Make every advertised capability accurately described, safe by default, and executable on supported Windows terminals before investing in richer autonomy.

## Scope

1. Reconcile version, README, changelog, MCP docs, package metadata, and release workflow with shipped behavior; remove all false completion claims.
2. Fix Windows UTF-8/non-TTY behavior, `%TEMP%`/memory-path diagnostics, and the MCP stdio transport on the supported Windows event loop.
3. Make file and shell tools opt-in for destructive operations, confine paths, use explicit command allowlists, validate working directories, and add audit events.
4. Make all configuration fields either effective or rejected with a clear error.

## Acceptance evidence

- `agentcli mcp` passes a subprocess JSON-RPC smoke test on Windows.
- A piped UTF-8 `--preset coding chat` loop runs without traceback.
- Destructive file/shell requests are denied unless a documented explicit policy enables them.
- Documentation says `0.7.0 alpha` (or the deliberately chosen version) until later release evidence exists.
