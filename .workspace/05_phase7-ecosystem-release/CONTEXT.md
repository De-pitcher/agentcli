# Phase 7 — Ecosystem Integration & Release

Status: PLANNED. Not started. Activate after Phase 6 PR merges.

## What this phase will address

1. **PyPI release**: version `1.0.0`, GitHub Actions release workflow, signed artifacts
2. **Plugin / tool-call interface**: allow external tools to be registered and called by the agent loop
3. **MCP server integration**: expose agentcli as an MCP-compatible server so other tools can call it
4. **Documentation site**: mkdocs or similar, auto-deployed from main
5. **Brew / winget packaging**: installable without pip for non-Python users

## Acceptance criteria (draft)

- `pip install agentcli` installs a working `agentcli` CLI
- `agentcli --version` outputs the correct semver
- Release workflow triggers on `git tag v*`, publishes to PyPI, creates GitHub release with sdist + wheel
- All existing tests still pass; coverage ≥ 85%
