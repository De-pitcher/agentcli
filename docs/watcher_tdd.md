# Autonomous Project Watcher & Continuous TDD Loop (`agentcli watch`)

`agentcli watch` provides a continuous development engine that watches your workspace files, runs test suites on save, diagnoses failures, and autonomously formulates, tests, and verifies fixes in isolated Git worktrees.

---

## ⚡ Quickstart

```bash
# Watch current directory, run tests on file save, and display candidate patches
agentcli watch

# Automatically apply verified patches to the working tree
agentcli watch --auto-apply

# Custom test command and debounce interval
agentcli watch --test-cmd "pytest tests/unit -q" --debounce 1.5 --cooldown 5.0
```

---

## 🏗️ Architecture & Continuous Loop

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Workspace File System                           │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ File change (debounced)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       FileWatcher Engine                               │
│      (Prunes .git, pycache, .pytest_cache; detects real mutations)     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Trigger
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      ContinuousTDDRunner                               │
│                 Executes test command (e.g. pytest)                    │
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │ Tests Pass                     │ Tests Fail
                    ▼                                ▼
            ┌──────────────┐             ┌───────────────────────┐
            │  Status: OK  │             │ Extract Error Summary │
            │  Await next  │             └───────────┬───────────┘
            │  file change │                         │
            └──────────────┘                         ▼
                                         ┌───────────────────────┐
                                         │ Git Worktree Sandbox  │
                                         │ (Isolated verification│
                                         │  of LLM repair patch) │
                                         └───────────┬───────────┘
                                                     │ Tests Pass in Sandbox
                                                     ▼
                                         ┌───────────────────────┐
                                         │ Present / Apply Patch │
                                         └───────────────────────┘
```

---

## 🛡️ Git Worktree Sandboxing

To ensure broken code or erroneous AI suggestions never pollute your working tree:
1. **Isolated Worktree**: When a test fails, `WorktreeManager` checks out a temporary Git worktree under `.agentcli_worktrees/`.
2. **Safe Mutation & Verification**: The `AgentLoop` applies the patch and runs the test suite *inside the isolated worktree*.
3. **Clean Promotion**:
   - If tests **pass** in the worktree:
     - With `--auto-apply`: The patch is applied to the main workspace.
     - Without `--auto-apply`: A colorized unified diff is displayed for developer review.
   - If tests **fail** in the worktree: The worktree is discarded with no impact on the developer's working directory.

---

## ⚙️ Configuration & Flags

In `agentcli.toml`:

```toml
[watcher]
enabled = true
paths = ["."]
ignore_patterns = [".git", "__pycache__", ".pytest_cache", "dist", "build"]
debounce_seconds = 1.0
cooldown_seconds = 5.0
test_command = "pytest"
auto_apply = false
max_repair_iterations = 3
max_cost_usd = 0.50
budget_tier = "low"
```

### Command-Line Arguments
| Flag | Default | Description |
|---|---|---|
| `--test-cmd` | `"pytest"` | Test runner command to execute on change |
| `--debounce` | `1.0` | Seconds to buffer rapid file changes before firing test run |
| `--cooldown` | `5.0` | Thermal cooldown delay between successive repair attempts |
| `--auto-apply` | `False` | Automatically apply verified repair patches to the active worktree |
| `--max-iterations` | `3` | Maximum autonomous repair attempts per test failure |
| `--max-cost` | `None` | Hard ceiling on USD API spend during watcher session |
| `--budget` | `"low"` | Budget tier for repair routing (`low`, `medium`, `high`) |
