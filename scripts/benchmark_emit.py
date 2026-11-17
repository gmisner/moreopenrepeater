#!/usr/bin/env python3
"""What one `Emitter.emit()` call costs, and what a flood does to it.

The emit path (docs/contracts/emit-path.md) must be cheap enough to call from a
controller tick and must never wait on a sink. Run this on the target machine
(the smallest supported Pi) before anything real calls it:

    python scripts/benchmark_emit.py [--calls 200000]

It prints the cost of one call at p50, p99 and worst case: once at a steady
2000 events a second into a healthy sink (nothing should be dropped), and once
as an unpaced flood into a sink that never returns (the call must stay cheap
and everything that does not fit is counted as dropped). Compare the numbers with the
20 ms audio block budget. This measures this machine only; it proves nothing
about another one.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from contracts import Emitter, ListSink, Severity  # noqa: E402


class StuckSink:
    name = "stuck"

    def __init__(self) -> None:
        self.release = threading.Event()

    def write(self, lines: list[str]) -> None:
        self.release.wait()


def measure(label: str, emitter: Emitter, calls: int, rate: int = 0) -> None:
    samples = []
    for i in range(calls):
        start = time.perf_counter_ns()
        emitter.emit(Severity.INFO, "tx.started", n=i, state="receiving")
        samples.append(time.perf_counter_ns() - start)
        if rate and i % 100 == 99:
            time.sleep(100 / rate)
    samples.sort()
    us = lambda ns: ns / 1000  # noqa: E731
    dropped = sum(
        emitter.metrics.get("mor_log_events_dropped_total", reason="queue_full", severity=s.name) for s in Severity
    )
    print(
        f"{label:<14} p50 {us(statistics.median(samples)):7.2f} us   p99 {us(samples[int(len(samples) * .99)]):7.2f} us"
        f"   max {us(samples[-1]):9.2f} us   dropped {int(dropped)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=int, default=200_000, help="calls in the flood run (steady run uses a tenth)")
    calls = parser.parse_args().calls

    healthy = Emitter("bench", "0", [ListSink()], capacity=1000, drain_interval_s=0.05)
    healthy.start()
    measure("steady 2k/s", healthy, calls // 10, rate=2000)
    healthy.stop()

    stuck = StuckSink()
    blocked = Emitter("bench", "0", [stuck], capacity=1000, drain_interval_s=0.05)
    blocked.start()
    measure("flood, stuck", blocked, calls)
    stuck.release.set()
    blocked.stop()


if __name__ == "__main__":
    main()
