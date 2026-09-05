# Production Readiness & Host Execution Guidelines

This document details the production validation benchmarks, hardware profiles, security fences, and concurrency constraints enforced across `agentcli`.

---

## 💻 Developer Host Profile: `peregrine001`

`agentcli` is engineered to operate reliably on developer laptops with tight thermal and battery envelopes:

- **Processor**: Intel Core i7-8650U @ 1.90GHz (4 physical cores / 8 threads, 15W ULV mobile TDP).
- **RAM**: 32 GB.
- **Operating System**: Windows 11 64-bit with PowerShell terminal.

### 🚨 Local Execution Rules (Mandatory)
1. **Single-Worker Limit (`maxWorkers: 1` / `--runInBand`)**:
   - Sustained multi-threaded CPU load triggers thermal throttling on 15W mobile processors.
   - All test runs and background pipelines MUST use single-threaded sequential execution.
2. **Sequential Tool Commands**:
   - Background compilation or test commands must run sequentially.
   - Stale background tasks must be cleanly terminated upon timeout or cancellation.

---

## 🛡️ Security Fences & Sandboxing

`agentcli` implements multi-layer defense-in-depth security boundaries:

```
┌───────────────────────────────────────────────────────────┐
│                     USER REQUEST                          │
└─────────────────────────────┬─────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────┐
│               Security Barrier & Input Sanity             │
├───────────────────────────────────────────────────────────┤
│ 1. Path Traversal Guard: Prevents `../` escaping root     │
│ 2. Read-Only Sandbox: Blocks mutations unless authorized  │
│ 3. Shell Denylist: Blocks `rm -rf`, format, fork bombs    │
│ 4. Environment Sanity: Blocks `LD_PRELOAD`, DLL hijacking │
└─────────────────────────────┬─────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────┐
│                 Target Sub-Agent Execution                │
└───────────────────────────────────────────────────────────┘
```

### 1. FileOps Security
- **Path Traversal Denial**: Any relative path attempting to navigate outside the active project root (e.g. `../../etc/passwd` or `..\..\Windows`) is rejected with a validation error.
- **Explicit Write Authorization**: Write, delete, and directory creation operations are disabled by default unless `--allow-write` is provided or confirmed via interactive prompt.

### 2. Shell Execution Sandboxing
- **Command Denylist**: High-risk system destructive commands (`rm -rf /`, `mkfs`, `format`, `dd`) are blocked before process spawn.
- **Environment Variable Sanitization**: Dangerous injection vectors (such as `LD_PRELOAD`, `LD_LIBRARY_PATH`, `DYLD_INSERT_LIBRARIES`) are stripped from sub-process environments.

---

## 💾 SQLite Concurrency & File Locking

- **WAL Mode**: Databases use Write-Ahead Logging (`PRAGMA journal_mode=WAL;`) for high concurrency and low-latency reads.
- **Thread Safety**: Connection sharing is coordinated via re-entrant thread locks (`threading.RLock`) with automatic connection teardown (`store.close()`) to avoid Windows handle locking.

---

## 📊 Live Production Stress Audit Verification

In Phase 22 completion audit, the codebase passed all 21 verification vectors across 9 core subsystems:

- **Vector 1**: Configuration, CLI Parsing, and Presets Deep Merge (PASS)
- **Vector 2**: Model Routing, Intent Classification, and Remote Fallbacks (PASS)
- **Vector 3**: Sub-Agents, Read-Only Fencing, and Git Grounding (PASS)
- **Vector 4**: Plan-Act-Reflect Loop, Max Iterations, and Cost Ceilings (PASS)
- **Vector 5**: SQLite Persistence and ContextCache LRU Eviction (PASS)
- **Vector 6**: MCP Server and OpenRouter Function Calling Translation (PASS)
- **Vector 7**: Swarm Peer Delegation and Consensus Engine (PASS)
- **Vector 8**: Console Rendering and Interactive TUI Dashboard (PASS)
- **Vector 9**: Project Watcher and Continuous TDD Loop (PASS)

**Current Test Suite**: 366 passed tests (86.92% coverage), 0 Ruff lint errors, 0 Mypy type issues across 46 modules.
