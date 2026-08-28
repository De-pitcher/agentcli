"""Benchmark for session retrieval latency and context caching token savings (Phase 5)."""

import tempfile
import time
from pathlib import Path

from agentcli.files import expand_file_references
from agentcli.memory.budget import estimate_tokens
from agentcli.memory.cache import ContextCache
from agentcli.memory.store import MemoryStore


def benchmark_session_load_latency() -> float:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "bench.db"
        with MemoryStore(db_path) as store:
            # Seed session with 50 messages
            store.create_session("bench_sess_1", title="Benchmark Session")
            for i in range(50):
                role = "user" if i % 2 == 0 else "assistant"
                store.append_message(
                    "bench_sess_1", role, f"Message turn {i} with some sample content."
                )

        # Measure session retrieval latency across 100 runs
        latencies = []
        for _ in range(100):
            with MemoryStore(db_path) as store:
                t0 = time.perf_counter()
                sess = store.get_session("bench_sess_1")
                msgs = store.get_messages("bench_sess_1")
                t1 = time.perf_counter()
                assert sess is not None
                assert len(msgs) == 50
                latencies.append((t1 - t0) * 1000)

        avg_latency = sum(latencies) / len(latencies)
        p95_latency = sorted(latencies)[95]
        print("Session Load Latency (50 messages, 100 iterations):")
        print(f"  Average: {avg_latency:.3f} ms")
        print(f"  p95:     {p95_latency:.3f} ms")
        return avg_latency


def benchmark_context_caching() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "large_context.py"
        file_content = "# Sample Python Code\n" + "def compute(x):\n    return x * 2\n" * 100
        file_path.write_text(file_content, encoding="utf-8")

        raw_tokens = estimate_tokens(file_content)
        cache = ContextCache(enabled=True)

        # Turn 1: Initial read (Cache Miss)
        t0 = time.perf_counter()
        _ = expand_file_references(f"Analyze @{file_path}", cache=cache)
        t1 = time.perf_counter()
        turn1_time = (t1 - t0) * 1000

        # Turn 2: Unchanged file reference (Cache Hit)
        t2 = time.perf_counter()
        _ = expand_file_references(f"Analyze @{file_path}", cache=cache)
        t3 = time.perf_counter()
        turn2_time = (t3 - t2) * 1000

        print("\nContext Caching Benchmark:")
        print(f"  File size: {len(file_content)} chars (~{raw_tokens} tokens)")
        print(
            f"  Turn 1 (Disk Read + Hash + Format): {turn1_time:.3f} ms, Cache hits: {cache.hits}"
        )
        print(
            f"  Turn 2 (In-Memory Cache Hit):       {turn2_time:.3f} ms, Cache hits: {cache.hits}"
        )
        print(f"  Speedup factor: {turn1_time / max(0.001, turn2_time):.1f}x")


if __name__ == "__main__":
    benchmark_session_load_latency()
    benchmark_context_caching()
