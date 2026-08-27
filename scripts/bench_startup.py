"""
Rough startup-time benchmark: measures cold-process time to import agentcli
and build the argument parser (i.e. everything before a network call would
happen). Run with: python scripts/bench_startup.py

This is a proxy for "time to interactive" — the real chat REPL prompt
appears within a few ms of this point, network latency aside.
"""

import subprocess
import sys
import time


def main() -> None:
    runs = 5
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        subprocess.run(
            [sys.executable, "-c", "import agentcli.cli; agentcli.cli.build_parser()"],
            check=True,
        )
        times.append(time.perf_counter() - start)
    avg = sum(times) / len(times)
    print(f"Avg cold-import + parser build time over {runs} runs: {avg * 1000:.1f}ms")
    print(f"Min: {min(times) * 1000:.1f}ms  Max: {max(times) * 1000:.1f}ms")


if __name__ == "__main__":
    main()
