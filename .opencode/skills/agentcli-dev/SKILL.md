---
name: agentcli-dev
description: >
  Development workflow for the agentcli Python project (OpenRouter-backed AI
  chat CLI). Use whenever implementing features, fixing bugs, reviewing code,
  or running tests in C:\Users\sam\Documents\sparkz\agentcli. Covers the
  protected-main PR workflow, quality gates, architecture map, testing
  patterns, and known gotchas.
---

# agentcli Development Workflow

## Project Snapshot

| Attribute | Detail |
|---|---|
| **Stack** | Python 3.11–3.14 (dev machine: 3.14.3), single runtime dep: `httpx` |
| **Type** | Interactive CLI chat REPL against OpenRouter (OpenAI-compatible API) |
| **Repo** | `C:\Users\sam\Documents\sparkz\agentcli` — own git repo, remote `git@github-second:De-pitcher/agentcli.git` (GitHub account: **De-pitcher**) |
| **Quality gates** | `pytest` (85% coverage floor, currently ~94%), `ruff check .`, `mypy .` (strict) |
| **CI** | GitHub Actions matrix: ubuntu+windows × py3.11/3.12/3.13/3.14 → 8 checks named `test (<os>, <ver>)` |
| **Main branch** | PROTECTED: PRs required, all 8 CI checks must pass (exact-match contexts), linear history, no force-push, `enforce_admins: true` (owner cannot bypass) |
| **Shell** | ⚠️ Windows PowerShell ONLY — never bash syntax |
| **gh CLI** | Not on PATH — call as `"C:\Program Files\GitHub CLI\gh.exe"` (authed as De-pitcher) |
| **Git identity** | Repo-local: `De-pitcher <emmanwa000@gmail.com>` (three zeros — verified email; the two-zero variant is a known typo, never reintroduce it) |

---

## Phase 0 — Understand the Task

1. **Map the blast radius** — module ownership below; changes to
   `openrouter_client.py` affect everything downstream.
2. **Check CHANGELOG.md `[Unreleased]`** — every user-visible change needs an
   entry there before merge.
3. **Check AGENTS.md** at workspace root for cross-project lessons (agentcli
   lessons are #46–#49).

### Architecture map

```
agentcli/
├── cli.py                 # argparse entry, REPL loop, exit codes, logging setup
├── config.py              # TOML dataclasses; resolution: ./agentcli.toml > $AGENTCLI_CONFIG > %APPDATA%\agentcli\config.toml
├── files.py               # @path token expansion → fenced context blocks (200KB cap)
├── openrouter_client.py   # async httpx client, SSE streaming, retry/backoff (429/5xx/network)
├── exit_codes.py          # 0 SUCCESS | 1 GENERAL_ERROR | 2 CONFIG_ERROR | 3 USER_INTERRUPT
└── __main__.py            # python -m agentcli support
tests/                     # mirrors modules; no live network in tests ever
```

Data flow per turn: `input()` (multi-line via trailing `\`) →
`expand_file_references()` → append to history → trim to `history_turns*2+1`
(preserving system message) → `client.chat_stream()` → print deltas → append
assistant reply.

---

## Phase 1 — Branch (NEVER commit to main directly)

```powershell
git -C "C:\Users\sam\Documents\sparkz\agentcli" checkout -b <type>/<topic> main
```

| Prefix | When |
|---|---|
| `feat/<topic>` | New functionality (Phase 2+ work) |
| `fix/<topic>` | Bug fixes |
| `chore/<topic>` | Tooling, CI, docs, deps |

---

## Phase 2 — Implement

### Conventions (match existing code exactly)

- **Dataclasses** for config; **no pydantic** — keep deps at `httpx` only.
- **One pooled `httpx.AsyncClient`** per client instance, reused across calls;
  never construct per request. Close via `aclose()` / async context manager.
- **Exit codes are a contract**: config/missing-file errors → 2, user
  interrupt → 3. Any new failure mode must map to an existing code or extend
  `ExitCode` deliberately.
- **Config precedence is a feature**: file values beat code defaults. When
  changing a default (e.g. `DEFAULT_MODEL`), remember existing user config
  files still hold the old value — surface this in the PR description.
- **Errors are user-facing**: `OpenRouterError` messages go to the REPL via
  `logger.error`; keep them actionable (include status + body snippet).
- httpx logger is silenced to WARNING in `main()` — do not remove; the REPL
  must not show `INFO: HTTP Request:` lines.

### Ruff strictness notes

- `except ...: pass` triggers S110; blind `except Exception` triggers BLE001.
  In best-effort cleanup handlers, log at debug level and catch **concrete
  types** (e.g. `(asyncio.CancelledError, KeyboardInterrupt, httpx.HTTPError)`).

---

## Phase 3 — Quality Gates (run ALL, in this order, from repo root)

```powershell
python -m pytest          # expect: 40+ passed, "Required test coverage of 85% reached" (~94%)
python -m ruff check .    # expect: All checks passed!
python -m mypy .          # expect: Success: no issues found in 13 source files
```

If any fails — stop and fix before committing. mypy runs strict
(`disallow_untyped_defs`) on source; tests are relaxed.

---

## Phase 4 — Testing Patterns

**Never hit the live API in tests.** Two established patterns:

### 1. Client tests — `httpx.MockTransport` handler style

```python
def handler(request):
    async def stream():
        yield b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    return httpx.Response(200, content=stream())


transport = httpx.MockTransport(handler)
config = OpenRouterConfig(api_key_env="DUMMY")  # monkeypatch.setenv first
client = OpenRouterClient(config)
client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)
```

For retry tests: monkeypatch `asyncio.sleep` with an async no-op, count
handler invocations via `nonlocal`, assert exhaustion raises the right
subclass (`RateLimitedError` vs `OpenRouterError`).

### 2. REPL tests — FakeClient + monkeypatched builtins

```python
monkeypatch.setattr("builtins.input", fake_input)  # pops from a list
monkeypatch.setattr("builtins.print", lambda *a, **k: None)
monkeypatch.setattr("agentcli.cli.OpenRouterClient", lambda _: FakeClient())
```

> **Rule**: patch `agentcli.config.find_config_path` (NOT
> `agentcli.cli.find_config_path`) — cli.py calls `init_config()` which
> resolves paths inside the config module at call time.

**Bug fix rule**: reproduce the bug in a test first, then fix. Every crash or
wrong-exit-code found in manual verification gets a regression test.

---

## Phase 5 — PR → CI → Merge

```powershell
git -C "C:\Users\sam\Documents\sparkz\agentcli" push -u origin <branch>
& "C:\Program Files\GitHub CLI\gh.exe" pr create --repo De-pitcher/agentcli --base main --head <branch> --title "..." --body "..."
& "C:\Program Files\GitHub CLI\gh.exe" pr checks <n> --repo De-pitcher/agentcli --watch --interval 20
& "C:\Program Files\GitHub CLI\gh.exe" pr merge <n> --repo De-pitcher/agentcli --squash --delete-branch
git -C "C:\Users\sam\Documents\sparkz\agentcli" checkout main; git -C "C:\Users\sam\Documents\sparkz\agentcli" pull --ff-only
```

- All 8 matrix checks must be green; merge is **squash only** (linear history).
- If merge is rejected with "base branch policy prohibits the merge": run
  `gh pr view <n> --json mergeStateStatus` — BLOCKED = a required check name
  doesn't match (see gotchas), BEHIND = rebase onto origin/main.

---

## Quick Reference: Known Gotchas

| Symptom | Root Cause | Fix |
|---|---|---|
| Model 404 "unavailable for free" despite fresh install | User config file (`%APPDATA%\agentcli\config.toml` or `./agentcli.toml`) overrides code default with stale slug | Update the TOML; config-beats-default is by design |
| Model slug invalid | Free models get retired regularly | Verify live: `GET https://openrouter.ai/api/v1/models`, filter `:free` (see openrouter-api skill) |
| Ctrl+C dumps traceback, exit code ≠ 3 | Real SIGINT surfaces during async cleanup, not just at `input()` | Handled: best-effort `aclose()` + `main()` maps SIGINT → exit 3. Keep both layers |
| PR merge blocked, all checks green | Required check contexts are exact-match `test (<os>, <ver>)` strings | Compare against `statusCheckRollup`; update protection contexts if matrix changed |
| Direct push to main "bypassed rule violations" warning but succeeded | `enforce_admins` was false | Must stay `true` — verify with a real rejected push, never trust settings alone |
| `agentcli` command not found after `pip install -e .` | User-site Scripts dir not on PATH | Use `python -m agentcli`, or add `%APPDATA%\Python\Python314\Scripts` to PATH |
| New config field ignored at runtime | `load_config()` reads explicit keys only | Add to dataclass AND `load_config()` AND `DEFAULT_CONFIG_TOML` |
| Startup bench ~600ms+ vs README claim | `asyncio` import chain dominates; editable-install finder adds ~400ms | Known; lazy-import optimization deferred to optimization phase |
| Coverage drops below 85% in CI | New code without tests | `pytest` locally enforces the same floor — never bypass `--cov-fail-under` |
| CRLF warnings on commit | Windows autocrlf | Harmless; do not "fix" by committing .gitattributes changes casually |

---

## Roadmap Context

Phase 1 (foundation) ✅ → **Phase 2: multi-model routing (next)** → Phase 3:
sub-agents → Phase 4: custom agent core → Phase 5: memory & context →
Phase 6: optimization → Phase 7: ecosystem & release.

Phase 2 design constraints and acceptance criteria live in the
`agentcli-routing-design` skill. OpenRouter API facts live in the
`openrouter-api` skill. Read both before writing Phase 2 code.
