# Quality Gates

Every PR must pass all of these before merge. Run locally before opening a PR.

## Commands (Windows PowerShell — always use `python -m <module>` form)

```powershell
# From C:\Users\sam\Documents\sparkz\agentcli\

python -m pytest --cov                  # must reach >= 85% total coverage
python -m ruff check .                  # zero errors
python -m ruff format --check .         # zero formatting drift
python -m mypy .                        # zero errors (strict mode)
python -m pip_audit --local             # zero known vulnerabilities in THIS project
python -m build                         # succeeds, zero deprecation warnings
```

## CI matrix

8 required checks, all must be green before squash-merge:

| OS | Python |
|---|---|
| ubuntu-latest | 3.11 |
| ubuntu-latest | 3.12 |
| ubuntu-latest | 3.13 |
| ubuntu-latest | 3.14 |
| windows-latest | 3.11 |
| windows-latest | 3.12 |
| windows-latest | 3.13 |
| windows-latest | 3.14 |

## Coverage floor

85% total. Currently ~94%. New code must include tests. If a PR drops coverage, add tests — do not lower the threshold.

## Pre-commit hooks

`pre-commit install` sets up ruff + mypy locally. They run on every commit. CI is the final arbiter.

## Known CI quirks

- `pip-audit --local` — the `--local` flag is mandatory; without it the runner's pre-installed packages cause false positives.
- `python -m build` — must produce zero `SetuptoolsDeprecationWarning` lines. License field must be `license = "MIT"` (SPDX string), not the deprecated table form.
- `asyncio.sleep` must be mocked in any test that exercises retry backoff — otherwise tests take real wall-clock time.
- Module-level `setInterval`-equivalent timers must call `.unref()` or equivalent to prevent open handle warnings in pytest.

## Test file map

```
tests/
├── test_classifier.py       — classify() function, all three categories + priority rules
├── test_cli.py              — run_chat(), run_config(), main() — monkeypatches agentcli.session.OpenRouterClient
├── test_config.py           — load_config(), init_config(), ConfigError on bad types
├── test_files.py            — expand_file_references(), FileReadError, size limit
├── test_openrouter_client.py — OpenRouterClient streaming, retries, transport errors, models array
├── test_registry.py         — ModelRegistry candidates(), mark_success/failure(), cooldown
├── test_router.py           — Router.decide(), fallback caps, cooling models, unknown category
└── test_session.py          — AgentSession history trimming, system message preservation, pop
```

## Monkeypatch target for OpenRouterClient

`agentcli.session.OpenRouterClient` — not `agentcli.cli.OpenRouterClient` (moved in post-audit refactor).
