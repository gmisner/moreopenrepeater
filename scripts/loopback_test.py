#!/usr/bin/env python3
"""End-to-end check of the live audio engine through a loopback device.

Plays a known test sequence into a loopback audio device (BlackHole on a
Mac: `brew install blackhole-2ch`) while the engine listens on the same
device, then checks the engine saw what was sent:

  1. a voice-level burst  -> carrier (VOX COS) opens, then closes
  2. DTMF "147#"          -> those four digits, in order
  3. a 100.0 Hz CTCSS tone under audio -> the tone is decoded

No repeater controller is attached, so the engine never transmits and its
(silent) output can safely go back to the same loopback device.

    PYTHONPATH=src .venv/bin/python scripts/loopback_test.py [--device "BlackHole 2ch"]
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audio_io.audio_stream import sd  # noqa: E402
from audio_io.engine import AudioEngine  # noqa: E402
from audio_io.processor import SILENCE_DB, AudioProcessor, ProcessorSettings  # noqa: E402
from controller.events import COSChanged, CTCSSChanged, DTMFDigit  # noqa: E402
from dsp.goertzel import ctcss_tone, dtmf_tone  # noqa: E402

RATE = 16000
BLOCK = RATE // 50
DIGITS = "147#"
CTCSS_HZ = 100.0


def test_signal(rate: int) -> np.ndarray:
    def n(duration: float) -> int:
        return int(rate * duration)

    def voice(duration: float, level: float = 0.2) -> np.ndarray:
        t = np.arange(n(duration)) / rate
        return level * (0.6 * np.sin(2 * np.pi * 700 * t) + 0.4 * np.sin(2 * np.pi * 1250 * t))

    parts = [np.zeros(n(0.5)), voice(1.5), np.zeros(n(1.2)), voice(0.3, level=0.1)]
    for digit in DIGITS:
        parts += [dtmf_tone(digit, rate, n(0.2)), voice(0.15, level=0.1)]
    parts += [np.zeros(n(1.2))]
    parts += [voice(3.0, level=0.1) + ctcss_tone(CTCSS_HZ, rate, n(3.0), amplitude=0.15), np.zeros(n(1.5))]
    return np.concatenate(parts).astype(np.float32)


def evaluate(events: list) -> list[tuple[str, bool]]:
    cos = [e.active for e in events if isinstance(e, COSChanged)]
    digits = "".join(e.digit for e in events if isinstance(e, DTMFDigit))
    tones = [e.tone_hz for e in events if isinstance(e, CTCSSChanged) and e.tone_hz is not None]
    return [
        (f"carrier opened and closed for each of the 3 bursts (got {cos})", cos == [True, False] * 3),
        (f"DTMF digits {DIGITS!r} (got {digits!r})", digits == DIGITS),
        (f"CTCSS {CTCSS_HZ} Hz decoded (got {tones})", tones == [CTCSS_HZ]),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", default="BlackHole 2ch", help="loopback device name (default: %(default)s)")
    args = parser.parse_args()

    if sd is None:
        print("sounddevice/PortAudio isn't available -- pip install sounddevice", file=sys.stderr)
        return 2

    events: list[tuple[float, object]] = []
    lock = threading.Lock()
    started = time.monotonic()

    def on_events(batch):
        with lock:
            events.extend((time.monotonic() - started, e) for e in batch)

    processor = AudioProcessor(ProcessorSettings(RATE, cos_source="vox", vox_threshold_db=-35.0, vox_hold=0.4))
    engine = AudioEngine(processor, BLOCK, on_events, input_device=args.device, output_device=args.device)
    # Play at the device's own rate: a second stream at a different rate
    # makes CoreAudio try to switch the device mid-use and fail.
    play_rate = int(sd.query_devices(args.device, "output")["default_samplerate"])
    loudest = SILENCE_DB
    engine.start()
    try:
        time.sleep(0.3)
        signal = test_signal(play_rate)
        print(f"Playing {len(signal) / play_rate:.1f}s test sequence into {args.device!r}...")
        sd.play(signal, samplerate=play_rate, device=args.device)
        deadline = time.monotonic() + len(signal) / play_rate + 0.5
        while time.monotonic() < deadline:
            loudest = max(loudest, processor.rx_level_db)
            time.sleep(0.02)
        sd.wait()
    finally:
        engine.stop()

    for at, event in events:
        print(f"  {at:6.2f}s  {event}")
    if loudest <= SILENCE_DB:
        print(
            "\nThe engine's input was pure digital silence. On macOS that almost always means the app\n"
            "running this (Terminal, iTerm, Cursor...) lacks Microphone permission: System Settings >\n"
            "Privacy & Security > Microphone, enable it, then quit and reopen the app.",
        )

    checks = evaluate([e for _, e in events]) + [
        (
            f"no audio glitches (dropped {engine.dropped_input_blocks}, starved {engine.starved_output_blocks})",
            engine.dropped_input_blocks == 0,
        ),
    ]
    print()
    for label, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
