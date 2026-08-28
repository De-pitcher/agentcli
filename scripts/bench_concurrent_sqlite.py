"""Benchmark testing event-loop concurrency & message-bus latency during synchronous SQLite writes."""

import asyncio
import tempfile
import time
from pathlib import Path

from agentcli.memory.store import MemoryStore
from agentcli.subagents.bus import Message, MessageBus, MessageType


async def run_concurrent_subagent_benchmark() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "concurrent.db"
        store = MemoryStore(db_path)
        store.create_session("sess_concurrent", title="Concurrent Test")

        bus = MessageBus()
        bus_latencies: list[float] = []

        # Background subscriber measuring message delivery latency
        async def on_message(msg: Message) -> None:
            send_time = msg.payload.get("send_time", 0.0)
            recv_time = time.perf_counter()
            bus_latencies.append((recv_time - send_time) * 1000)

        bus.subscribe(MessageType.CUSTOM, on_message)

        # Producer sending 100 messages concurrently every 1ms
        async def producer() -> None:
            for i in range(100):
                msg = Message(
                    type=MessageType.CUSTOM,
                    source="agent_a",
                    payload={"send_time": time.perf_counter(), "index": i},
                )
                await bus.publish(msg)
                await asyncio.sleep(0.001)

        # Worker performing synchronous SQLite writes on the same event loop
        async def db_writer() -> None:
            for i in range(20):
                # Simulating session write directly on event loop
                store.append_message("sess_concurrent", "user", f"Turn content {i}")
                await asyncio.sleep(0.005)

        t0 = time.perf_counter()
        await asyncio.gather(producer(), db_writer())
        t1 = time.perf_counter()

        store.close()

        avg_latency = sum(bus_latencies) / len(bus_latencies)
        max_latency = max(bus_latencies)
        p95_latency = sorted(bus_latencies)[int(len(bus_latencies) * 0.95)]

        print("Message Bus Latency under concurrent SQLite writes (100 messages, 20 writes):")
        print(f"  Average message delivery latency: {avg_latency:.3f} ms")
        print(f"  p95 message delivery latency:     {p95_latency:.3f} ms")
        print(f"  Max message delivery latency:     {max_latency:.3f} ms")
        print(f"  Total time:                       {(t1 - t0) * 1000:.3f} ms")


if __name__ == "__main__":
    asyncio.run(run_concurrent_subagent_benchmark())
