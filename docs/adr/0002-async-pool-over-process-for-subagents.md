# ADR 0002: In-Process Async Task Pool vs Multiprocessing for Sub-Agents

## Status
Accepted (Phase 3, binding)

## Context
Sub-agents need to coordinate concurrently (e.g. running code analysis while planning next steps). The options were:
1. Spawning separate OS processes via `multiprocessing` or subprocesses.
2. Spawning concurrent in-process `asyncio.Task` workers coordinating over an in-memory `MessageBus` and shared reference-counted `ContextPool`.

## Decision
Use `asyncio.Task` coroutines with `SubAgentSpawner` and an in-memory `MessageBus`. Sub-agents are lightweight asynchronous objects sharing connection pools and in-memory caches.

## Consequences
- **Positive**: Near-zero inter-process communication (IPC) serialization overhead, shared memory context pool with O(1) reads, instant task cancellation on SIGINT.
- **Positive**: Low memory footprint (~0.77 MB heap, ~28 MB process RSS) running 5+ concurrent agents.
- **Negative**: CPU-bound operations in sub-agents must yield to event loop or offload to threadpool to avoid blocking I/O.
