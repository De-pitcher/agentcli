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

## 🔀 Branching & PR Workflow

1. Branch from `main` using standard naming conventions:
   - `feat/<topic>` — new capabilities
   - `fix/<topic>` — bug fixes
   - `chore/<topic>` — tooling, CI, documentation
2. Run local quality gates before pushing:
   ```bash
   pytest --cov
   ruff check .
   ruff format --check .
   mypy .
   pip-audit --local
   python -m build
   ```
3. Open a pull request against `main`. All CI checks (Ubuntu & Windows across Python 3.11–3.14) must pass.

---

## 🐛 Reporting Issues & Feature Requests

Please use the issue templates under `.github/ISSUE_TEMPLATE/`. Include your OS, Python version, configuration preset, and error trace if applicable.
