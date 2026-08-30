# agentcli Performance Benchmarks & Methodology

This document outlines the performance benchmarks, measurement methodology, and resource utilization characteristics of `agentcli`.

---

## 🔬 Benchmark Methodology

Benchmarks are executed via [`scripts/profile_and_bench.py`](../scripts/profile_and_bench.py) across three isolated workloads:

1. **Single-Turn Local Pipeline**: Iterates through message classification (`classify`), candidate selection and fallback determination (`Router.decide`), and dynamic history window trimming (`trim_history_to_budget`) for 3,000 turns.
2. **LRU ContextCache File Access**: Executes 2,000 file context requests across 40 distinct mock files with a capacity ceiling of 30 items, testing hash calculation, mtime verification, and LRU eviction pressure.
3. **Concurrent 5-Agent Subsystem**: Spawns 5 asynchronous sub-agent workers concurrently executing 100 tasks through the in-memory `MessageBus` and persisting message state to the SQLite `MemoryStore` via `asyncio.to_thread`.

---

## 📊 Summary of Measured Results

| Metric | Result | Methodology / Tool | Notes |
| :--- | :--- | :--- | :--- |
| **Single-Turn Local Orchestration** | **0.23 ms / turn** | `time.perf_counter` (3,000 iterations) | Pure local framework latency; excludes network roundtrip |
| **LRU ContextCache Retrieval** | **0.87 ms / access** | `time.perf_counter` (2,000 accesses, 98% hit rate) | Path resolution caching eliminates Windows `realpath` syscalls |
| **Concurrent 5-Agent Throughput** | **0.60 s** total | `asyncio.gather` (100 tasks + SQLite persistence) | Evaluates spawner, bus, and async DB non-blocking performance |
| **Python Traced Heap Allocation** | **0.77 MB** peak | `tracemalloc.get_traced_memory()` | Python object allocations only |
| **Total OS Process Resident Set Size (RSS)** | **~28 MB** | OS process counters / Task Manager | Real working set memory (interpreter, DLLs, heap) |

---

## 💡 Framework Overhead vs Network Latency

- **Framework Execution Overhead**: Under normal conditions, `agentcli` consumes **< 1 millisecond** of CPU time to classify user intent, select optimal model routing, apply token budgets, and dispatch requests.
- **End-to-End Latency**: The user-perceived response time in `agentcli chat` is almost entirely governed by OpenRouter's upstream API processing, model generation speed (tokens/second), and network transit time (typically 200ms – 2,000ms).

---

## 💻 Hardware Budget & Laptop Constraints

`agentcli` is specifically engineered to run comfortably on resource-constrained development machines alongside heavier developer tools (IDEs, compilers, local LLM servers). With a real operating system process footprint of **~28 MB**, `agentcli` uses less than **15%** of its 200 MB maximum design budget.
