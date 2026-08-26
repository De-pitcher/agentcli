# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added — Phase 1: Foundation
- Interactive chat REPL (`agentcli chat`) against any OpenRouter model.
- `@path/to/file` inline context injection, plus `--file` for session preload.
- TOML configuration with project-local, env-override, and platform-default
  resolution (`agentcli config init` / `agentcli config show`).
- Async, connection-pooled OpenRouter client with SSE streaming and
  retry/backoff on 429 and 5xx responses.
- Open-source scaffolding: MIT license, CONTRIBUTING guide, GitHub Actions
  CI (Ubuntu + Windows, Python 3.11/3.12), issue template.
- Startup-time benchmark script.
- Hardened Phase 1 to a professional MVP standard with:
  - Test coverage for cli.py and openrouter_client.py (85% floor enforced).
  - Static typing enforcement with mypy and a py.typed marker.
  - Release hygiene updates: classifiers in pyproject.toml, CODE_OF_CONDUCT.md, SECURITY.md, and .pre-commit-config.yaml.
  - UX improvements: --version, --verbose for logging, python -m agentcli support, and mid-stream interrupt handling.
