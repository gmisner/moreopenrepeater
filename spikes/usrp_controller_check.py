"""One-off check: the controller as node 1999's radio, through api.allstar_audio.

Not part of the test suite. Runs the real RepeaterService, AudioProcessor
and AllStarAudio inside the ASL3 VM, with a simulated radio in place of the
sound card. Nodes as in usrp_asl3_spike.py, with 1999 set up as in
docs/allstar.md, linked to 1998, and nothing else bound to 34001/34002.
The Mac home directory is read-only in the VM and importing `api` writes a
log under data/, so run it from a copy, in a venv with the app's
dependencies:

    cp -r src spikes web /tmp/morcheck/ && cd /tmp/morcheck
    PYTHONPATH=src /tmp/morvenv/bin/python spikes/usrp_controller_check.py

Confirmed 2026-09-28 (ASL3, app_rpt 3.10.5): out 1.96 s of 2 s, in 2.02 s,
doubling 1.96 s, and the courtesy tone waits for both to unkey.

  * Out: a user keys up on the repeater with a 1 kHz tone for two seconds;
    the controller repeats it, so it should reach 1998's transmitter.
  * In: 1998's receiver gets a 600 Hz tone for two seconds; 1999 sends it to
    the controller, which should key up and transmit it.

With `doubling`, the user keys up (21-23 s) while 1998's station is talking
(20-24 s): a half-duplex node drops the user; see how much 1 kHz 1998 sends.
"""
from __future__ import annotations

import asyncio
import socket
import sys
import threading
import time

import numpy as np

from api.allstar_audio import AllStarAudio, UsrpSettings
from api.service import RepeaterService
from audio_io.processor import AudioProcessor, ProcessorSettings
from controller.state_machine import RepeaterConfig
from link.usrp import HEADER, encode_voice

RATE = 16000
BLOCK = RATE // 50


class Radio:
    """Stands in for LiveAudio: the processor, driven every 20 ms."""

    def __init__(self, service: RepeaterService) -> None:
        self.processor = AudioProcessor(ProcessorSettings(sample_rate=RATE, cos_source="external"))
        self.service = service
        self.transmitted: list[tuple[float, float]] = []  # (time, rms) while PTT

    def set_ptt(self, active: bool) -> None:
        self.processor.set_ptt(active)

    def set_repeating(self, repeating: bool) -> None:
        self.processor.set_repeating(repeating)

    def set_link(self, link) -> None:
        self.processor.set_link(link)

    def set_patch(self, patch) -> None: pass
    def arm_parrot(self) -> bool: return False
    def play(self, clip) -> None: pass

    async def run(self, user: tuple[float, float]) -> None:
        loop = asyncio.get_running_loop()
        start = next_at = loop.time()
        n = 0
        while True:
            t = loop.time() - start
            keyed = user[0] <= t < user[1]
            self.processor.set_external_cos(keyed)
            block = 0.3 * np.sin(2 * np.pi * 1000 * (np.arange(BLOCK) + n * BLOCK) / RATE) if keyed else np.zeros(BLOCK)
            result = self.processor.process(block.astype(np.float32))
            if result.events:
                self.service.handle_audio_events(result.events)
            if result.transmitting:
                self.transmitted.append((t, float(np.sqrt(np.mean(result.out**2)))))
            if n % 5 == 0:
                self.service.tick()
            n += 1
            next_at += BLOCK / RATE
            await asyncio.sleep(max(0.0, next_at - loop.time()))


def listen_1998(heard: list, stop: threading.Event, start: float) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 34002))
    sock.settimeout(0.5)
    while not stop.is_set():
        try:
            data = sock.recv(1024)
        except socket.timeout:
            continue
        _eye, _seq, _mem, keyup, *_ = HEADER.unpack(data[: HEADER.size])
        pcm = np.frombuffer(data[HEADER.size :], dtype="<i2").astype(float)
        user = abs(np.dot(pcm, np.exp(-2j * np.pi * 1000 * np.arange(len(pcm)) / 8000))) / max(len(pcm), 1)
        heard.append((time.monotonic() - start, keyup, int(np.abs(pcm).max()) if len(pcm) else 0, user))
    sock.close()


def key_1998(at: float, seconds: float, start: float) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    time.sleep(max(0.0, start + at - time.monotonic()))
    t0 = time.monotonic()
    for seq in range(1, int(seconds * 50) + 1):
        i = np.arange(160) + seq * 160
        pcm = (8000 * np.sin(2 * np.pi * 600 * i / 8000)).astype("<i2").tobytes()
        sock.sendto(encode_voice(seq, pcm), ("127.0.0.1", 32002))
        time.sleep(max(0.0, t0 + seq * 0.02 - time.monotonic()))
    sock.close()


async def main() -> None:
    service = RepeaterService(config=RepeaterConfig())
    radio = Radio(service)
    service.audio_output = radio
    link = AllStarAudio(service, UsrpSettings("127.0.0.1", 34001, "127.0.0.1", 32001))
    await link.start()
    assert link.status()["running"], link.status()

    start = time.monotonic()
    heard: list = []
    stop = threading.Event()
    listener = threading.Thread(target=listen_1998, args=(heard, stop, start))
    listener.start()
    doubling = sys.argv[1:] == ["doubling"]
    keyer = threading.Thread(target=key_1998, args=(20.0, 4.0 if doubling else 2.0, start))
    keyer.start()
    states: list[tuple[float, str]] = []

    async def watch() -> None:
        while True:
            state = service.controller.state
            if not states or states[-1][1] != state:
                states.append((round(time.monotonic() - start, 2), state))
            await asyncio.sleep(0.02)

    tasks = [asyncio.create_task(radio.run(user=(21.0, 23.0) if doubling else (2.0, 4.0))), asyncio.create_task(watch())]
    await asyncio.sleep(35)
    for task in tasks:
        task.cancel()
    await link.stop()
    stop.set()
    listener.join()
    keyer.join()

    if doubling:
        user = [t for t, keyup, _peak, one_k in heard if keyup and one_k > 1000]
        print(f"doubling: 1998 transmitted the user for {len(user) * 0.02:.2f}s of 2s"
              + (f" from {user[0]:.2f}s to {user[-1]:.2f}s" if user else ""))
    out = [(t, peak) for t, keyup, peak, _ in heard if keyup and peak > 4000 and t < 15]
    print(f"out: 1998 transmitted {len(out)} loud packets ({len(out) * 0.02:.2f}s)"
          + (f" from {out[0][0]:.2f}s to {out[-1][0]:.2f}s" if out else ""))
    loud = [t for t, rms in radio.transmitted if 20 <= t < 30 and rms > 0.05]
    print(f"in: the repeater transmitted 1998's audio for {len(loud) * 0.02:.2f}s"
          + (f" from {loud[0]:.2f}s to {loud[-1]:.2f}s" if loud else ""))
    print("controller states:", states)


if __name__ == "__main__":
    asyncio.run(main())
