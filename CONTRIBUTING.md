# Contributing to agentcli

Thanks for considering a contribution. This project is early (Phase 1 of a
7-phase roadmap) so expect the internals to move fast.

## Getting set up

```bash
git clone https://github.com/De-pitcher/agentcli
cd agentcli
pip install -e ".[dev]"
pre-commit install
pytest
```

## Before opening a PR

- Run `pytest`, `ruff check .`, and `mypy .` locally (or rely on `pre-commit`). CI runs these on Ubuntu and Windows across Python 3.11 and 3.12. Ensure coverage remains above 85%.
- Keep new runtime dependencies to a minimum; this project optimizes for
  fast startup and low memory footprint so it can run alongside other CLI
  agents on modest hardware.
- Add or update tests for behavior changes.
- Explain the "why," not just the "what," in your PR description.

## Reporting issues

Use the issue templates under `.github/ISSUE_TEMPLATE/`. Include your OS,
Python version, and the model you were using if relevant — free-tier model
availability on OpenRouter changes often and is a common source of bugs
that aren't actually bugs in this codebase.

## Scope for now

Phase 1 is intentionally narrow: single-model chat, file context injection,
config. Routing, sub-agents, and the custom agent loop are coming in later
phases — PRs jumping ahead of the current phase are welcome as discussion
but may be deferred until the relevant phase lands.
