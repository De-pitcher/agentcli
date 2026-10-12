# Contributing to agentcli

Thanks for considering a contribution! `agentcli` is a budget-conscious, model-agnostic AI agent CLI built with pure Python and backed by OpenRouter.

---

## 🛠️ Development Setup

```bash
git clone https://github.com/De-pitcher/agentcli
cd agentcli
pip install -e ".[dev]"
pre-commit install
pytest
```

---

## 📐 Architecture & Principles

- **Pure Python, Zero Heavy Runtimes**: Standard library first; `httpx` is the sole external runtime dependency.
- **Budget-First Design**: All models default to free-tier OpenRouter endpoints with automatic cooldowns, dynamic token budgeting, and sub-second local latency.
- **Pluggable & Extensible**: New tools can be added via `ToolRegistry.register_callable` or external plugin files without modifying core code. See [docs/plugins.md](docs/plugins.md).
- **Architectural Decision Records (ADRs)**: Review [docs/adr/](docs/adr/) before proposing major structural changes.

---

## Issue ownership and pull requests

This process applies to everyone, including the repository owner and AI-assisted contributions.

1. Open an issue before implementation. State the problem, proposed scope, acceptance criteria and evidence needed.
2. Wait for maintainer triage. A maintainer assigns exactly one accountable GitHub user and labels the issue status:ready or status:in-progress. Comment on the issue if you want to take it; do not duplicate assigned work.
3. Use a separate branch or fork for that issue, for example fix/77-watcher-owned-boundary. Keep the PR limited to its accepted scope. Record AI worker/reviewer roles in the issue; agents do not replace the accountable GitHub assignee.
4. Open a PR with exactly one standalone line: Closes #NUMBER. Its primary issue must be open, approved by a status label, and assigned to the PR author. Put other references on Related lines.
5. Include source-bound validation and known limitations. All required CI matrix checks and contribution-policy must pass; resolve review conversations. Issue assignment/label changes recheck open linked PRs.
6. Merge through the PR. The rules apply to administrators too; direct main updates, force pushes and deletion are prohibited. Independent human approval will become required when another maintainer joins. Until then, PRs and checks are mandatory without an approving-review count.

Bots follow the same ownership process; there is no silent bypass. Local edits cannot be prevented by GitHub, but merging untracked contributions is blocked. An administrator can still deliberately change repository settings; permanent separation requires another person to control administrative access.

Run focused tests while iterating; use the full documented gate at the phase boundary. Keep local workloads sequential per AGENTS.md. Validate the policy logic with node .github/tests/contribution-policy.test.cjs.

---

## 🐛 Reporting Issues & Feature Requests

Please use the issue templates under `.github/ISSUE_TEMPLATE/`. Include your OS, Python version, configuration preset, and error trace if applicable.
