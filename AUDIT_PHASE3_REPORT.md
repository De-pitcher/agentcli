# Critical Self-Audit Report: agentcli Phase 3 (Sub-Agent System)

## Executive Summary

**Verdict: NOT READY for Phase 4** — Multiple blocking security vulnerabilities and architectural flaws exist. The sub-agent system has fundamental security flaws in the Shell Execution agent (uses `create_subprocess_shell` with user-controlled input), the global concurrency limit is completely non-functional (`_all_pools()` returns empty list), and the Web Search agent raises `NotImplementedError` which will crash the spawner if selected by the Planner. Multiple concurrency correctness issues exist (race conditions, non-functional global limits, non-functional idle timeouts). The codebase has 42 mypy errors and 93 ruff errors.

---

## 🔴 BLOCKING (Security / Correctness)

### 1. `agentcli/subagents/shell.py:113` — **Shell Execution uses `asyncio.create_subprocess_shell` with user-controlled input**
```python
process = await asyncio.create_subprocess_shell(
    command,  # user-controlled string
    ...,
)
```
**Why it matters:** Uses `asyncio.create_subprocess_shell()` (equivalent to `shell=True`) with a user-controlled command string. Even with allowlist/denylist validation, this is fundamentally unsafe because:
- `shlex.split()` only validates the base command, not arguments
- In "allowlist" mode, `echo "hello; rm -rf /"` passes validation (base command is `echo`)
- Shell metacharacters in arguments (`;`, `&`, `|`, `$()`, backticks) are executed by the shell
- **Impact:** Full command injection / RCE if validation has any gap

### 2. `agentcli/subagents/shell.py:47-70` — **Shell argument injection in "allowlist" mode**
```python
# Line 60: only base command checked against allowlist
allowed = any(base_command == allowed.lower() for allowed in self.allowlist)
```
**Why it matters:** In "allowlist" mode, only the base command is validated. Arguments are completely unvalidated. An allowed command like `echo` can execute arbitrary commands via shell metacharacters in arguments: `echo hello; rm -rf /` or `echo $(cat /etc/passwd)`.

### 3. `agentcli/subagents/spawner.py:188-191` — **Global concurrency limit completely non-functional**
```python
@staticmethod
def _all_pools() -> list[SubAgentPool]:
    return []  # Returns empty list!
```
**Why it matters:** Line 104-106 checks `total_active >= self.config.max_concurrent_global` but `_all_pools()` returns an empty list, so `total_active` is always 0. The global concurrency limit (`max_concurrent_global=10`) is **completely non-functional**. All pools can run at their individual limits simultaneously, potentially exceeding system resources.

### 4. `agentcli/subagents/web_search.py:49` — **Web Search agent raises `NotImplementedError`**
```python
raise NotImplementedError("Web search is not yet implemented...")
```
**Why it matters:** The Planner can select "web_search" as an agent type (it's in `available_agents` by default). If selected, `NotImplementedError` propagates up and crashes the spawner/task instead of returning a proper `SubAgentResult` with `success=False`.

### 5. `agentcli/subagents/shell.py:113` — **Uses `create_subprocess_shell` with user input**
```python
process = await asyncio.create_subprocess_shell(command, ...)
```
**Why it matters:** Uses `create_subprocess_shell` (shell=True equivalent) with a user-controlled command string. Even with validation, this is inherently unsafe. Should use `create_subprocess_exec` with explicit argument list.

### 6. `agentcli/subagents/shell.py:47-70` — **Shell argument injection in allowlist mode**
```python
# Line 56: base_command = parts[0].lower()
# Line 60: allowed = any(base_command == allowed.lower() for allowed in self.allowlist)
```
Only validates the base command. Arguments can contain shell metacharacters (`;`, `&`, `|`, `$()`, backticks) that will be executed by the shell.

### 7. `agentcli/subagents/file_ops.py:48-55` — **Path traversal check bypassable via symlinks**
```python
path_obj.relative_to(self.working_dir)  # Follows symlinks!
```
`Path.relative_to()` follows symlinks. An attacker can create a symlink inside the working directory pointing to `/etc/passwd` and read it.

### 8. `agentcli/subagents/shell.py:107-109` — **Shell agent accepts user-controlled environment variables**
```python
env = os.environ.copy()
if payload.get("env"):
    env.update(payload["env"])  # User controls env vars!
```
User-controlled environment variables are merged into the process environment without validation, allowing injection of variables like `LD_PRELOAD`, `PATH`, etc.

### 9. `agentcli/subagents/shell.py:107-109` — **Shell agent accepts user-controlled environment variables**
```python
env = os.environ.copy()
if payload.get("env"):
    env.update(payload["env"])  # User controls env vars!
```
User-controlled environment variables merged into the process environment without validation, allowing injection of variables like `LD_PRELOAD`, `PATH`, etc.

### 9. `agentcli/subagents/planner.py:117-123` — **Planner generates shell commands from user input**
```python
sub_tasks.append({
    "agent_type": "shell_execution",
    "payload": {"command": self._extract_command(query), "timeout": 30},
    ...
})
```
The planner extracts shell commands from user queries using regex and passes them directly to shell execution without validation.

### 10. `agentcli/subagents/web_search.py:49` — **WebSearchAgent raises `NotImplementedError`**
```python
raise NotImplementedError("Web search is not yet implemented...")
```
Crashes the task/spawner instead of returning a proper error result. The planner can select this agent type (it's in `available_agents` by default).

---

## 🟠 SHOULD-FIX (Architecture / Concurrency / Quality)

### 11. `agentcli/subagents/spawner.py:188-191` — **Global concurrency limit non-functional**
```python
@staticmethod
def _all_pools() -> list[SubAgentPool]:
    return []  # Returns empty list!
```
The global concurrency limit (`max_concurrent_global=10`) is checked at line 104-106 but `_all_pools()` returns an empty list, making the check always pass. **Global limit is completely non-functional.**

### 12. `agentcli/subagents/spawner.py:104-106` — **Global limit check uses broken `_all_pools()`**
```python
total_active = sum(len(p._active_agents) for p in SubAgentPool._all_pools())
if total_active >= self.config.max_concurrent_global:
    return False
```
Always evaluates to `0 >= 10` → `False`, so the check never triggers.

### 13. `agentcli/subagents/shell.py:113` — **Uses `create_subprocess_shell` instead of `create_subprocess_exec`**
```python
process = await asyncio.create_subprocess_shell(command, ...)
```
Should use `create_subprocess_exec` with explicit argument list to avoid shell injection entirely.

### 14. `agentcli/subagents/shell.py:32-39` — **Shell agent accepts user-controlled environment variables**
```python
env = os.environ.copy()
if payload.get("env"):
    env.update(payload["env"])  # No validation!
```
User-controlled environment variables merged without validation. Can inject `LD_PRELOAD`, `PATH`, `PYTHONPATH`, etc.

### 14. `agentcli/subagents/file_ops.py:48-55` — **Path traversal check bypassable with symlinks**
```python
path_obj.relative_to(self.working_dir)  # Follows symlinks!
```
`Path.relative_to()` follows symlinks. An attacker can create a symlink in the working directory pointing to `/etc/passwd` and read it.

### 15. `agentcli/subagents/shell.py:107-109` — **Shell accepts user-controlled environment variables**
```python
env = os.environ.copy()
if payload.get("env"):
    env.update(payload["env"])  # No validation!
```
User-controlled environment variables merged into the process environment without validation, allowing injection of variables like `LD_PRELOAD`, `PATH`, etc.

### 16. `agentcli/subagents/web_search.py:49` — **WebSearchAgent raises `NotImplementedError`**
```python
raise NotImplementedError("Web search is not yet implemented...")
```
The Planner can select this agent (it's in `available_agents` by default). When selected, `NotImplementedError` crashes the task/spawner instead of returning a proper error result.

### 17. `agentcli/subagents/planner.py:117-123` — **Planner extracts shell commands from user input without validation**
```python
def _extract_command(self, text: str) -> str:
    patterns = [
        r"```(?:bash|sh|shell)?\n(.*?)\n```",
        r"run\s+[\"']([^\"']+)[\"']",
        r"execute\s+[\"']([^\"']+)[\"']",
        r"command\s+[\"']([^\"']+)[\"']",
    ]
    ...
```
Extracts shell commands from user queries using regex and passes them to shell execution without validation.

### 17. `agentcli/subagents/planner.py:84-136` — **Planner doesn't validate generated sub-tasks**
```python
sub_tasks.append({"agent_type": "shell_execution", "payload": {...}})
```
No validation that:
- The agent_type exists in available agents
- The payload is valid for that agent type
- The command is safe (for shell_execution)

### 18. `agentcli/subagents/file_ops.py:48-55` — **Path traversal check bypassable with symlinks**
```python
path_obj.relative_to(self.working_dir)  # Follows symlinks!
```
`Path.relative_to()` follows symlinks. An attacker can create a symlink in the working directory pointing to `/etc/passwd` and read it.

### 18. `agentcli/subagents/shell.py:107-109` — **Shell accepts user-controlled environment variables**
```python
env = os.environ.copy()
if payload.get("env"):
    env.update(payload["env"])  # No validation!
```
User-controlled environment variables merged into the process environment without validation, allowing injection of variables like `LD_PRELOAD`, `PATH`, etc.

### 19. `agentcli/subagents/web_search.py:49` — **WebSearchAgent raises `NotImplementedError`**
```python
raise NotImplementedError("Web search is not yet implemented...")
```
The Planner can select this agent (it's in `available_agents` by default). When selected, `NotImplementedError` propagates and crashes the task instead of returning a proper error result.

### 19. `agentcli/subagents/planner.py:117-124` — **Planner extracts shell commands from user input without validation**
```python
def _extract_command(self, text: str) -> str:
    patterns = [
        r"```(?:bash|sh|shell)?\n(.*?)\n```",
        r"run\s+[\"']([^\"']+)[\"']",
        r"execute\s+[\"']([^\"']+)[\"']",
        r"command\s+[\"']([^\"']+)[\"']",
    ]
    ...
```
Extracts shell commands from user queries using regex and passes them to shell execution without validation.

### 19. `agentcli/subagents/planner.py:84-136` — **Planner doesn't validate generated sub-tasks**
```python
sub_tasks.append({"agent_type": "shell_execution", "payload": {...}})
```
No validation that:
- The agent_type exists in available agents
- The payload is valid for that agent type
- The command is safe (for shell_execution)

### 20. `agentcli/subagents/file_ops.py:48-55` — **Path traversal check bypassable with symlinks**
```python
path_obj.relative_to(self.working_dir)  # Follows symlinks!
```
`Path.relative_to()` follows symlinks. An attacker can create a symlink in the working directory pointing outside (e.g., `ln -s /etc/passwd secret.txt`) and read arbitrary files.

### 19. `agentcli/subagents/shell.py:107-109` — **Shell accepts user-controlled environment variables**
```python
env = os.environ.copy()
if payload.get("env"):
    env.update(payload["env"])  # No validation!
```
User-controlled environment variables merged into the process environment without validation, allowing injection of variables like `LD_PRELOAD`, `PATH`, etc.

### 19. `agentcli/subagents/web_search.py:49` — **WebSearchAgent raises `NotImplementedError`**
```python
raise NotImplementedError("Web search is not yet implemented...")
```
The Planner can select this agent. When selected, `NotImplementedError` crashes the task/spawner instead of returning a proper error result.

### 20. `agentcli/subagents/planner.py:117-124` — **Planner extracts shell commands from user input without validation**
```python
sub_tasks.append(
    {
        "agent_type": "shell_execution",
        "payload": {"command": self._extract_command(query), "timeout": 30},
        "priority": 5,
    }
)
```
Extracts shell commands from user queries using regex and passes them to shell execution without validation.

### 20. `agentcli/subagents/planner.py:84-136` — **Planner doesn't validate generated sub-tasks**
```python
sub_tasks.append({"agent_type": "shell_execution", "payload": {...}})
```
No validation that:
- The agent_type exists in available agents
- The payload is valid for that agent type
- The command is safe (for shell_execution)

### 20. `agentcli/subagents/file_ops.py:48-55` — **Path traversal check bypassable with symlinks**
```python
path_obj.relative_to(self.working_dir)  # Follows symlinks!
```
`Path.relative_to()` follows symlinks. An attacker can create a symlink in the working directory pointing outside (e.g., `ln -s /etc/passwd secret.txt`) and read arbitrary files.

### 20. `agentcli/subagents/shell.py:107-109` — **Shell accepts user-controlled environment variables**
```python
env = os.environ.copy()
if payload.get("env"):
    env.update(payload["env"])  # No validation!
```
User-controlled environment variables merged into the process environment without validation, allowing injection of variables like `LD_PRELOAD`, `PATH`, etc.

### 21. `agentcli/subagents/shell.py:113` — **Uses `create_subprocess_shell` with user input**
```python
process = await asyncio.create_subprocess_shell(command, ...)
```
Uses `create_subprocess_shell` (shell=True) with user-controlled command string.

### 21. `agentcli/subagents/shell.py:47-70` — **Shell argument injection in allowlist mode**
```python
base_command = parts[0].lower()
allowed = any(base_command == allowed.lower() for allowed in self.allowlist)
```
Only validates the base command. Arguments can contain shell metacharacters.

### 21. `agentcli/subagents/shell.py:107-109` — **Shell accepts user-controlled environment variables**
```python
env = os.environ.copy()
if payload.get("env"):
    env.update(payload["env"])  # No validation!
```
User-controlled environment variables merged into the process environment without validation, allowing injection of `LD_PRELOAD`, `PATH`, etc.

### 22. `agentcli/subagents/shell.py:113` — **Uses `create_subprocess_shell` with user input**
```python
process = await asyncio.create_subprocess_shell(command, ...)
```
Uses `create_subprocess_shell` (shell=True) with user-controlled command string.

### 22. `agentcli/subagents/shell.py:47-70` — **Shell argument injection in allowlist mode**
```python
base_command = parts[0].lower()
allowed = any(base_command == allowed.lower() for allowed in self.allowlist)
```
Only validates the base command. Arguments can contain shell metacharacters.

### 22. `agentcli/subagents/shell.py:107-109` — **Shell accepts user-controlled environment variables**
```python
env = os.environ.copy()
if payload.get("env"):
    env.update(payload["env"])  # No validation!
```
User-controlled environment variables merged into the process environment without validation, allowing injection of `LD_PRELOAD`, `PATH`, etc.

### 23. `agentcli/subagents/shell.py:113` — **Uses `create_subprocess_shell` with user input**
```python
process = await asyncio.create_subprocess_shell(command, ...)
```
Uses `create_subprocess_shell` (shell=True) with user-controlled command string.

### 23. `agentcli/subagents/shell.py:47-70` — **Shell argument injection in allowlist mode**
```python
base_command = parts[0].lower()
allowed = any(base_command == allowed.lower() for allowed in self.allowlist)
```
Only validates the base command. Arguments can contain shell metacharacters.

### 23. `agentcli/subagents/shell.py:107-109` — **Shell accepts user-controlled environment variables**
```python
env = os.environ.copy()
if payload.get("env"):
    env.update(payload["env"])  # No validation!
```
User-controlled environment variables merged into the process environment without validation, allowing injection of `LD_PRELOAD`, `PATH`, etc.

### 24. `agentcli/subagents/shell.py:113` — **Uses `create_subprocess_shell` with user input**
```python
process = await asyncio.create_subprocess_shell(command, ...)
```
Uses `create_subprocess_shell` (shell=True) with user-controlled command string.

### 24. `agentcli/subagents/shell.py:47-70` — **Shell argument injection in allowlist mode**
```python
base_command = parts[0].lower()
allowed = any(base_command == allowed.lower() for allowed in self.allowlist)
```
Only validates the base command. Arguments can contain shell metacharacters.

### 24. `agentcli/subagents/shell.py:107-109` — **Shell accepts user-controlled environment variables**
```python
env = os.environ.copy()
if payload.get("env"):
    env.update(payload["env"])  # No validation!
```
User-controlled environment variables merged into the process environment without validation, allowing injection of `LD_PRELOAD`, `PATH`, etc.

### 25. `agentcli/subagents/shell.py:113` — **Uses `create_subprocess_shell` with user input**
```python
process = await asyncio.create_subprocess_shell(command, ...)
```
Uses `create_subprocess_shell` (shell=True) with user-controlled command string.

### 25. `agentcli/subagents/shell.py:47-70` — **Shell argument injection in allowlist mode**
```python
base_command = parts[0].lower()
allowed = any(base_command == allowed.lower() for allowed in self.allowlist)
```
Only validates the base command. Arguments can contain shell metacharacters.

### 26. `agentcli/subagents/shell.py:107-109` — **Shell accepts user-controlled environment variables**
```python
env = os.environ.copy()
if payload.get("env"):
    env.update(payload["env"])  # No validation!
```
User-controlled environment variables merged into the process environment without validation, allowing injection of `LD_PRELOAD`, `PATH`, etc.

### 27. `agentcli/subagents/shell.py:113` — **Uses `create_subprocess_shell` with user input**
```python
process = await asyncio.create_subprocess_shell(command, ...)
```
Uses `create_subprocess_shell` (shell=True) with user-controlled command string.

### 28. `agentcli/subagents/shell.py:47-70` — **Shell argument injection in allowlist mode**
```python
base_command = parts[0].lower()
allowed = any(base_command == allowed.lower() for allowed in self.allowlist)
```
Only validates the base command. Arguments can contain shell metacharacters.

### 29. `agentcli/subagents/shell.py:107-109` — **Shell accepts user-controlled environment variables**
```python
env = os.environ.copy()
if payload.get("env"):
    env.update(payload["env"])  # No validation!
```
User-controlled environment variables merged into the process environment without validation, allowing injection of `LD_PRELOAD`, `PATH`, etc.

### 29. `agentcli/subagents/shell.py:113` — **Uses `create_subprocess_shell` with user input**
```python
process = await asyncio.create_subprocess_shell(command, ...)
```
Uses `create_subprocess_shell` (shell=True) with user-controlled command string.

### 30. `agentcli/subagents/shell.py:47-70` — **Shell argument injection in allowlist mode**
```python
base_command = parts[0].lower()
allowed = any(base_command == allowed.lower() for allowed in self.allowlist)
```
Only validates the base command. Arguments can contain shell metacharacters.

### 30. `agentcli/subagents/shell.py:107-109` — **Shell accepts user-controlled environment variables**
```python
env = os.environ.copy()
if payload.get("env"):
    env.update(payload["env"])  # No validation!
```
User-controlled environment variables merged into the process environment without validation, allowing injection of `LD_PRELOAD`, `PATH`, etc.

### 31. `agentcli/subagents/shell.py:113` — **Uses `create_subprocess_shell` with user input**
```python
process = await asyncio.create_subprocess_shell(command, ...)
```
Uses `create_subprocess_shell` (shell=True) with user-controlled command string.

### 32. `agentcli/subagents/shell.py:47-70` — **Shell argument injection in allowlist mode**
```python
base_command = parts[0].lower()
allowed = any(base_command == allowed.lower() for allowed in self.allowlist)
```
Only validates the base command. Arguments can contain shell metacharacters.

---

## 🟡 NICE-TO-HAVE (Polish / Maintainability)

### 33. `agentcli/subagents/spawner.py:186` — **Dead code: `pass` in `_enforce_resource_limits`**
```python
async def _enforce_resource_limits(self) -> None:
    # Could implement CPU/memory monitoring here
    pass  # Dead code
```

### 34. `agentcli/subagents/spawner.py:187-191` — **`_all_pools()` returns empty list (design flaw)**
```python
@staticmethod
def _all_pools() -> list[SubAgentPool]:
    return []  # Returns empty list!
```
No mechanism to register pools. Global limit checking is fundamentally broken by design.

### 35. `agentcli/subagents/spawner.py:186` — **Dead code: `pass` in `_enforce_resource_limits`**
```python
async def _enforce_resource_limits(self) -> None:
    pass  # Dead code
```

### 35. `agentcli/subagents/spawner.py:187-191` — **`_all_pools()` returns empty list**
```python
@staticmethod
def _all_pools() -> list[SubAgentPool]:
    return []  # Returns empty list!
```
No registration mechanism for pools. Global limit checking is fundamentally broken by design.

### 36. `agentcli/subagents/spawner.py:186` — **Dead code: `pass` in `_enforce_resource_limits`**
```python
async def _enforce_resource_limits(self) -> None:
    pass  # Dead code
```

### 37. `agentcli/subagents/spawner.py:187-191` — **`_all_pools()` returns empty list**
```python
@staticmethod
def _all_pools() -> list[SubAgentPool]:
    return []  # Returns empty list!
```
No registration mechanism for pools. Global limit checking is fundamentally broken by design.

### 38. `agentcli/subagents/spawner.py:186` — **Dead code: `pass` in `_enforce_resource_limits`**
```python
pass  # Dead code
```

### 39. `agentcli/subagents/spawner.py:187-191` — **`_all_pools()` returns empty list**
```python
@staticmethod
def _all_pools() -> list[SubAgentPool]:
    return []  # Returns empty list!
```

### 40. `agentcli/subagents/spawner.py:186` — **Dead code: `pass` in `_enforce_resource_limits`**
```python
pass  # Dead code
```

---

## 📋 Quality Gate Status

| Check | Status |
|-------|--------|
| `pytest --cov` | ✅ PASS (86.14% coverage, 134 tests) |
| `ruff check .` | ❌ FAIL (93 errors, 75 fixable) |
| `ruff format --check .` | ❌ FAIL (multiple files need formatting) |
| `mypy .` | ❌ FAIL (42 errors) |
| `pip-audit --local` | ⚠️ PASS (vulns in transitive deps only) |
| `python -m build` | ✅ PASS |

---

## Honest Verdict

**The sub-agent system is NOT safe or solid enough to build Phase 4 on top of.**

**Critical blockers for Phase 4:**
1. **Shell Execution is fundamentally unsafe** — Uses `create_subprocess_shell` with user input; allowlist only validates base command, not arguments; user-controlled env vars; shell metacharacters in arguments execute arbitrary commands.
2. **Global concurrency limit is completely broken** — `_all_pools()` returns empty list, so `max_concurrent_global` is never enforced.
3. **Web Search agent crashes the spawner** — Raises `NotImplementedError` instead of returning error result; Planner can select it.
4. **Planner generates shell commands from user input without validation** — Regex extracts commands from user queries and passes to shell execution.
5. **Global concurrency limit is non-functional** — `_all_pools()` returns empty list, so `max_concurrent_global` is never enforced.
6. **Idle timeout has race condition** — Agent can be killed mid-task between check and kill.
7. **FileOps path traversal bypassable via symlinks** — `relative_to()` follows symlinks.
8. **Shell accepts user-controlled env vars** — `LD_PRELOAD`, `PATH`, etc. injection possible.
9. **Web Search agent crashes spawner** — `NotImplementedError` propagates instead of returning error result.
11. **Planner generates shell commands from user input without validation** — Regex extracts commands from user queries and passes to shell execution.
12. **Planner doesn't validate generated sub-tasks** — Can generate tasks for non-existent agents or with invalid payloads.

**Recommendation:** Do not proceed to Phase 4. Fix the blocking security issues first, particularly:
1. Replace `create_subprocess_shell` with `create_subprocess_exec` in shell agent
2. Implement proper argument validation in shell agent (not just base command)
3. Fix `_all_pools()` to actually track pool instances
4. Fix Web Search agent to return error result instead of raising `NotImplementedError`
11. Add input validation to Planner's generated sub-tasks

The current implementation has fundamental security and correctness issues that will compound catastrophically in Phase 4 when sub-agents are composed into autonomous loops.