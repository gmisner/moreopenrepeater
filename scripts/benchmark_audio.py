#!/usr/bin/env python3
"""How much of each 20 ms audio block the live engine's worker thread uses.

Runs the real per-block path -- resampling from a 48 kHz device, carrier
detection, CTCSS decode, DTMF decode (carrier held open so it always runs),
CTCSS encode with its high-pass filter, and repeat + clip mixing -- on
synthetic audio, with no sound devices. Run it on the target machine (e.g.
the Raspberry Pi) before trusting it with a repeater:

    .venv/bin/python scripts/benchmark_audio.py [--seconds 30] [--device-rate 48000]

Anything under ~25% of the budget at p99 leaves room for the web server,
text-to-speech rendering and the rest of the system.
"""
from __future__ import annotations

import argparse
import queue
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audio_io.engine import AudioEngine  # noqa: E402
from audio_io.processor import AudioProcessor, ProcessorSettings  # noqa: E402

RATE = 16000
BLOCK_SECONDS = 0.02


class _NoDevice:
    def __init__(self, sample_rate, block_size, input_queue, output_queue, device, device_rate):
        self.sample_rate = device_rate
        self.block_size = round(block_size * device_rate / sample_rate)
        self._output = output_queue
        self.dropped_input_blocks = self.starved_output_blocks = 0

    def start(self):
        pass

    def stop(self):
        pass

    def drain(self):
        while True:
            try:
                self._output.get_nowait()
            except queue.Empty:
                return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=30.0, help="audio to process (default: %(default)s)")
    parser.add_argument("--device-rate", type=int, default=48000, help="simulated sound card rate (default: %(default)s)")
    args = parser.parse_args()

    processor = AudioProcessor(
        ProcessorSettings(RATE, cos_source="vox", vox_threshold_db=-50, tx_ctcss_hz=100.0)
    )
    streams: list[_NoDevice] = []

    def stream_factory(**kwargs):
        streams.append(_NoDevice(device_rate=args.device_rate, **kwargs))
        return streams[-1]

    engine = AudioEngine(processor, int(RATE * BLOCK_SECONDS), on_events=lambda events: None, stream_factory=stream_factory)
    engine.start()
    engine._stop.set()  # the worker thread idles; blocks are driven below for timing
    stream = streams[0]

    device_block = stream.block_size
    t = np.arange(int(args.device_rate * args.seconds)) / args.device_rate
    rx = (0.2 * np.sin(2 * np.pi * 700 * t) + 0.05 * np.sin(2 * np.pi * 88.5 * t)).astype(np.float32)
    processor.set_ptt(True)
    processor.set_repeating(True)
    clip = np.full(RATE * 5, 0.1, dtype=np.float32)

    timings = []
    for n, start in enumerate(range(0, len(rx) - device_block + 1, device_block)):
        if n % 500 == 0:
            processor.play(clip)  # keep a clip mixing in too
        began = time.perf_counter()
        engine.process_one(rx[start : start + device_block])
        timings.append(time.perf_counter() - began)
        stream.drain()
    engine.stop()

    ms = np.array(timings[50:]) * 1000  # skip warm-up (caches, first allocations)
    budget = BLOCK_SECONDS * 1000
    print(f"{len(ms)} blocks of {budget:.0f} ms, device {args.device_rate} Hz -> {RATE} Hz")
    for label, value in (("mean", ms.mean()), ("p99", np.percentile(ms, 99)), ("max", ms.max())):
        print(f"  {label:>4}: {value:6.3f} ms  ({100 * value / budget:5.1f}% of budget)")
    p99_share = np.percentile(ms, 99) / budget
    verdict = "OK" if p99_share < 0.25 else "TIGHT" if p99_share < 0.6 else "TOO SLOW"
    print(f"\n{verdict}")
    return 0 if verdict != "TOO SLOW" else 1


if __name__ == "__main__":
    sys.exit(main())
