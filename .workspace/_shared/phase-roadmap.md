# agentcli 7-Phase Roadmap

**Current phase: Phase 4 — Custom Agent Core (Plan/Act/Reflect Loop)**
Active workspace: `02_phase4-agent-loop/`

## Phase status

| Phase | Name | Status | PR / branch |
|---|---|---|---|
| 1 | Foundation (CLI, config, files, streaming) | ✅ Merged | multiple PRs on main |
| 2 | Multi-model routing (classifier, registry, router, hybrid fallback) | ✅ Merged | PR #5 |
| 1-2 post-audit | AgentSession refactor, ConfigError, pip-audit scope, pyproject.toml | ✅ Merged | PR #6 |
| 3 | Sub-agent system (base, bus, spawner, pool, specialized agents) | ✅ Merged | PR #7 |
| **4** | **Custom agent core (plan/act/reflect loop)** | 🔄 Active | `02_phase4-agent-loop/` |
| 5 | Memory & context persistence | ⬜ Planned | `03_phase5-memory-context/` |
| 6 | Optimization (startup time, memory footprint) | ⬜ Planned | `04_phase6-optimization/` |
| 7 | Ecosystem integration & release | ⬜ Planned | `05_phase7-ecosystem-release/` |

## What "done" looks like for any phase

1. All existing tests still pass (`python -m pytest —cov` ≥ 85%)
2. `ruff check .` and `mypy .` clean
3. `pip-audit --local` and `python -m build` clean
4. PR opened on `feat/<topic>` or `chore/<topic>`, CI matrix (8 checks) green
5. PR squash-merged to `main`
6. `CHANGELOG.md` `[Unreleased]` section updated
7. This workspace: active phase folder moved to `_archive/`

## Non-negotiable scope boundary

Phase 3 delivers **sub-agent coordination only** — no memory persistence, no custom loop, no packaging. Scope creep into Phase 4+ is a PR-review block.
