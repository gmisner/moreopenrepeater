"""One-off spike: run api.autopatch against real Asterisk.

Not part of the test suite. Uses the same ASL3 Lima VM as
audiosocket_asl3_spike.py, with a `Local/` channel into a test dialplan
standing in for a SIP trunk, so no phone provider is needed. The radio side
is simulated: a 1 kHz tone goes "over the air" into the call while the
script records what the call sends back to transmit.

Setup in the VM (as root):

    asterisk -rx "module load res_audiosocket.so"
    asterisk -rx "module load app_audiosocket.so"
    # /etc/asterisk/custom/extensions.conf:
    [patchtest]
    exten => 5550000,1,Busy(3)
    exten => 5559999,1,Ringing()
     same => n,Wait(60)
    exten => 5552222,1,Answer()
     same => n,Playback(beep)
     same => n,Hangup()
    exten => _X.,1,Answer()
     same => n,Playback(hello-world)
     same => n,Echo()
    asterisk -rx "dialplan reload"

Lima forwards the VM's AMI port to 127.0.0.1:5038 on the Mac, and the VM
reaches the Mac as host.lima.internal (192.168.5.2), so:

    MOREOPENREPEATER_AMI_SECRET=... PYTHONPATH=src .venv/bin/python spikes/autopatch_asl3_spike.py

SPIKE_DIAL_STRING=PJSIP/{number}@mor-trunk calls through the SIP trunk
instead, against the fake provider set up by sip_trunk_asl3_spike.py.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time

import numpy as np

from api.autopatch import Autopatch, PatchSettings
from api.service import RepeaterService
from controller.state_machine import RepeaterConfig

RATE = 16000
BLOCK = 320


class RadioSide:
    """Stands in for the live audio engine: every 20 ms, one block of a
    1 kHz tone goes into the call and one block of call audio comes out."""

    def __init__(self):
        self.patch = None
        self.transmitted: list[np.ndarray] = []
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def set_patch(self, patch):
        self.patch = patch

    def set_ptt(self, active): pass
    def set_repeating(self, repeating): pass
    def play(self, clip): pass
    def arm_parrot(self): return False

    def _run(self):
        tone = (0.3 * np.sin(2 * np.pi * 1000 * np.arange(BLOCK) / RATE)).astype(np.float32)
        next_at = time.monotonic()
        while not self._stop.is_set():
            patch = self.patch
            if patch is not None:
                self.transmitted.append(patch.exchange(tone, carrier=True))
            next_at += BLOCK / RATE
            time.sleep(max(0.0, next_at - time.monotonic()))


def tone_level(samples: np.ndarray, hz: float) -> float:
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    freqs = np.fft.rfftfreq(len(samples), 1 / RATE)
    band = (freqs > hz - 30) & (freqs < hz + 30)
    return float(spectrum[band].max() / (spectrum.sum() + 1e-9))


async def wait_for(condition, timeout):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError
        await asyncio.sleep(0.05)


async def main():
    config = RepeaterConfig(
        autopatch_enabled=True,
        autopatch_dial_string=os.environ.get("SPIKE_DIAL_STRING", "Local/{number}@patchtest"),
        autopatch_allowed="NXXXXXX",
        autopatch_ring_seconds=8,
    )
    service = RepeaterService(config=config)
    radio = RadioSide()
    service.audio_output = radio
    settings = PatchSettings(
        "127.0.0.1", 5038, "admin", os.environ["MOREOPENREPEATER_AMI_SECRET"], "0.0.0.0", 9092, "192.168.5.2:9092"
    )
    patch = Autopatch(service, settings)
    await patch.start()

    print("1) answered call: hello-world, then echo of our 1 kHz tone")
    radio.transmitted.clear()
    assert patch.dial("5551234", "spike") is None
    await wait_for(lambda: patch.call and patch.call.state == "connected", 10)
    connected_at = len(radio.transmitted)
    await asyncio.sleep(4)
    patch.hangup("spike hung up")
    await wait_for(lambda: patch.call is None, 5)
    audio = np.concatenate(radio.transmitted[connected_at:])
    first, last = audio[: RATE], audio[-RATE:]
    print(f"   level first second {20 * np.log10(np.sqrt(np.mean(first ** 2)) + 1e-9):.1f} dBFS (hello-world)")
    print(f"   1 kHz share, last second: {tone_level(last, 1000):.3f} (echo), first second: {tone_level(first, 1000):.3f}")
    print(f"   result: {patch.last_call.result}")

    print("2) busy")
    patch.dial("5550000", "spike")
    await wait_for(lambda: patch.call is None, 15)
    print(f"   result: {patch.last_call.result}")

    print("3) far end answers, beeps, hangs up")
    patch.dial("5552222", "spike")
    await wait_for(lambda: patch.call is None, 15)
    print(f"   result: {patch.last_call.result}")

    print("4) cancel while ringing")
    patch.dial("5559999", "spike")
    await asyncio.sleep(2)
    patch.hangup("cancelled by spike")
    await wait_for(lambda: patch.call is None, 10)
    print(f"   result: {patch.last_call.result}")

    print("5) ringing past the ring timeout")
    patch.dial("5559999", "spike")
    await wait_for(lambda: patch.call is None, 30)
    print(f"   result: {patch.last_call.result}")

    print("spoken:", [c for c in service.controller.queued_announcements])
    await patch.stop()


if __name__ == "__main__":
    asyncio.run(main())
