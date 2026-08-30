# ADR 0004: Non-Blocking SQLite Store via asyncio.to_thread and Thread-Safe Locking

## Status
Accepted (Phase 6, binding)

## Context
Standard library `sqlite3` does synchronous disk I/O. Calling SQLite methods directly from async chat handlers can momentarily stall the Python event loop under heavy disk contention.

## Decision
1. Wrap all database operations in asynchronous helper methods (`acreate_session`, `aappend_message`, `aget_messages`, `aget_session_stats`) executed via `asyncio.to_thread`.
2. Configure SQLite connection with `check_same_thread=False` and WAL journal mode.
3. Protect every connection access with a re-entrant `threading.RLock()` to prevent C-level statement and cursor collisions across worker threads.

## Consequences
- **Positive**: Zero event-loop blocking during streaming responses or agentic turns.
- **Positive**: Safe concurrent reads and writes across multiple async worker tasks.
- **Negative**: Adds negligible thread dispatch and lock acquisition overhead (<10 microseconds per operation).
