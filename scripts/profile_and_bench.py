"""Phase 6 Profiling and Benchmark Script.

Profiles hot paths:
1. Single-turn chat orchestration (classify + route + trim + memory append).
2. ContextCache LRU bounding under memory pressure.
3. Multi-agent concurrent dispatch (spawner + message bus + memory writes).
4. Peak memory measurement via tracemalloc.
"""

from __future__ import annotations

import asyncio
import cProfile
import pstats
import shutil
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path

# Ensure agentcli is in sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agentcli.config import RoutingConfig
from agentcli.memory.budget import trim_history_to_budget
from agentcli.memory.cache import ContextCache
from agentcli.memory.store import MemoryStore
from agentcli.openrouter_client import ChatMessage
from agentcli.routing.classifier import classify
from agentcli.routing.registry import ModelRegistry
from agentcli.routing.router import Router
from agentcli.subagents.base import SubAgent, SubAgentConfig
from agentcli.subagents.bus import Message, MessageBus, MessageType
from agentcli.subagents.code_analyzer import CodeAnalyzerAgent
from agentcli.subagents.spawner import SubAgentSpawner


def profile_single_turn_pipeline(iterations: int = 3000) -> float:
    """Benchmark local single-turn pipeline: classification + routing + budget trimming."""
    registry = ModelRegistry(RoutingConfig())
    router = Router(registry, max_fallbacks=2)

    prompt = "def calculate_primes(n: int) -> list[int]:\n    # efficient sieve\n    pass"
    history = [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="How can I help you today?"),
        ChatMessage(role="user", content="Here is some code context: " + "print(1)\n" * 100),
    ]

    start = time.perf_counter()
    for _ in range(iterations):
        cat = classify(prompt)
        decision = router.decide(cat)
        assert decision is not None
        trimmed = trim_history_to_budget(history, max_context_tokens=4096, budget_ratio=0.75)
        assert len(trimmed) > 0

    elapsed = time.perf_counter() - start
    avg_us = (elapsed / iterations) * 1_000_000
    print(
        f"[Profiling] Single-turn pipeline: {iterations} turns in {elapsed:.4f}s ({avg_us:.2f} µs/turn)"
    )
    return avg_us


def profile_context_cache_lru(iterations: int = 2000) -> float:
    """Benchmark ContextCache under mixed hit and LRU eviction workload."""
    cache = ContextCache(enabled=True, max_entries=50, max_bytes=50 * 1024)
    tmpdir = tempfile.mkdtemp()
    try:
        files = []
        for i in range(100):
            p = Path(tmpdir) / f"file_{i}.txt"
            p.write_text(f"Content block for file {i} " * 20, encoding="utf-8")
            files.append(p)

        def dummy_reader(p: Path) -> str:
            return p.read_text(encoding="utf-8")

        start = time.perf_counter()
        # Access pattern: 80% hits on hot set (first 25 files), 20% cycling through 100 files
        for i in range(iterations):
            if i % 5 != 0:
                f = files[i % 25]
            else:
                f = files[i % len(files)]
            content, _hit = cache.get_or_read(f, dummy_reader)
            assert len(content) > 0
        elapsed = time.perf_counter() - start
        avg_us = (elapsed / iterations) * 1_000_000
        stats = cache.stats()
        print(
            f"[Profiling] ContextCache LRU: {iterations} accesses in {elapsed:.4f}s "
            f"({avg_us:.2f} µs/access, entries={stats['cached_entries']}, "
            f"hits={stats['hits']}, misses={stats['misses']})"
        )
        return avg_us
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def profile_concurrent_agents_load() -> None:
    """Simulate concurrent sub-agents communicating on the bus and writing memory."""
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "bench.db"
    store = MemoryStore(str(db_path))
    bus = MessageBus()
    config = {
        "code_analyzer": SubAgentConfig(enabled=True, max_concurrent=5),
    }
    factories: dict[str, Callable[[], SubAgent]] = {
        "code_analyzer": CodeAnalyzerAgent,
    }
    spawner = SubAgentSpawner(config=config, agent_factories=factories, message_bus=bus)

    await spawner.start()

    async def subagent_worker(agent_id: str, count: int) -> None:
        for j in range(count):
            msg = Message(type=MessageType.CUSTOM, source=agent_id, payload={"step": j})
            await bus.publish(msg)
            await store.aappend_message(
                session_id=f"sess_{agent_id}",
                role="assistant",
                content=f"Worker {agent_id} completed step {j}",
                token_count=25,
            )
            await asyncio.sleep(0.0001)

    try:
        start = time.perf_counter()
        workers = [subagent_worker(f"agent_{i}", 20) for i in range(5)]
        await asyncio.gather(*workers)
        elapsed = time.perf_counter() - start
        print(f"[Profiling] Concurrent 5-agent load (100 tasks + DB writes): {elapsed:.4f}s")
    finally:
        await spawner.shutdown()
        store.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def main() -> None:
    print("=" * 60)
    print("AGENTCLI PHASE 6 PROFILING & BENCHMARK SUITE")
    print("=" * 60)

    tracemalloc.start()

    # Detailed cProfile of the hot paths
    profiler = cProfile.Profile()
    profiler.enable()

    profile_single_turn_pipeline(3000)
    profile_context_cache_lru(2000)
    asyncio.run(profile_concurrent_agents_load())

    profiler.disable()

    current_mem, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print("-" * 60)
    print(f"Peak Memory Allocated: {peak_mem / (1024 * 1024):.3f} MB")
    print(f"Current Memory In Use: {current_mem / (1024 * 1024):.3f} MB")
    print("-" * 60)
    print("Top 10 CPU Time Consumers:")
    stats = pstats.Stats(profiler)
    stats.strip_dirs()
    stats.sort_stats(pstats.SortKey.CUMULATIVE)
    stats.print_stats(10)
    print("=" * 60)


if __name__ == "__main__":
    main()
