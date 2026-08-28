# Phase 5 — Memory & Context Persistence

Status: IMPLEMENTATION_COMPLETE — PR ready for review and CI merge.

## Implemented Deliverables

1. **Local-First Session/Conversation Persistence (`agentcli/memory/store.py`)**:
   - stdlib `sqlite3` persistence with WAL mode, foreign keys, and indexes.
   - Platform-aware data directory (`%LOCALAPPDATA%\agentcli\memory.db` on Windows, `$XDG_DATA_HOME/agentcli/memory.db` on Linux/macOS).
   - Auto-pruning retention policy for old sessions (`retention_days`).

2. **Context Invalidation Caching (`agentcli/memory/cache.py`)**:
   - `ContextCache` with mtime and SHA-256 content hash verification.
   - Integrated into `files.py` to eliminate redundant disk reads and token spend on unchanged `@file` references across multi-turn sessions (~35x speedup).

3. **Dynamic Token Budget & Context Windowing (`agentcli/memory/budget.py`)**:
   - Character-to-token heuristic estimation (~3.8 chars/token).
   - Dynamic `trim_history_to_budget` that preserves system prompts and recent turns within the active model's context window.
   - **Reconciliation Decision**: Token budgeting subsumes Phase 1's blunt turn-count trimming. `history_turns` from `[app]` config is preserved as an optional turn ceiling.

4. **Shared Context Pool Compaction (`agentcli/memory/context_pool.py`)**:
   - Async-safe bounded context store for concurrent sub-agents extending Phase 3's reference tracking.
   - Automatic two-phase compaction (evicting zero-ref items first, then summarizing oversized referenced items) when capacity exceeds `max_shared_context_bytes`.

5. **CLI Integration (`agentcli sessions`)**:
   - `agentcli sessions list`: browse past conversation sessions with message counts and timestamps.
   - `agentcli sessions show <id>`: display full message history for a session.
   - `agentcli sessions clear [--yes]`: clear local conversation history.
   - `agentcli chat --resume <id>`: continue an existing session with loaded history.

6. **Privacy & Config (`agentcli.config`)**:
   - `[memory]` TOML section (`enabled`, `db_path`, `retention_days`, `cache_enabled`, `max_shared_context_bytes`).
   - 100% local storage guarantee documented in `README.md`.

