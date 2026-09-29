"""One-off spike: carry a node's radio audio over app_rpt's USRP channel.

Not part of the test suite. Confirmed 2026-09-28 against ASL3
(asl3-asterisk 2:22.10.1+asl3-3.10.5-1.deb12, app_rpt with chan_usrp).

`rxchannel = AudioSocket/...` can't stand in for a node's radio: AudioSocket
carries audio only, and app_rpt learns that its receiver is active from
RADIO_KEY control frames. chan_usrp (what DVSwitch and SvxLink use) sends
them: it keys the node's receiver while voice packets arrive and unkeys a
moment after they stop, and marks packets it sends with keyup=1 while the
node transmits, then sends a header-only keyup=0 packet on unkey.

Wire format (app_rpt's channels/chan_usrp.c): a 32-byte header of "USRP"
and seven big-endian uint32s -- seq, memory, keyup, talkgroup, type (0 is
voice), mpxid, reserved -- then 160 samples of 8 kHz signed-linear audio in
the host's byte order (little-endian on x86 and ARM). chan_usrp only reads
the queue when app_rpt writes to the channel, which it does every 20 ms.

To reproduce, run inside the ASL3 VM (see audiosocket_asl3_spike.py):

    # Put both nodes on USRP, on ports of their own. ASL3 ships with
    # `noload = chan_usrp.so` in modules.conf; change it to load.
    #   [1999]  rxchannel = USRP/127.0.0.1:34001:32001
    #   [1998]  rxchannel = USRP/127.0.0.1:34002:32002
    sudo systemctl restart asterisk
    sudo asterisk -rx "rpt fun 1999 *31998"      # link them
    python3 spikes/usrp_asl3_spike.py

It plays a 1 kHz tone into node 1999's receiver for two seconds and checks
that it comes out of node 1998's transmitter, over the link, with keyup set;
`python3 spikes/usrp_asl3_spike.py reverse` goes from 1998 to 1999.

Node 1999 stands in for the controller's node, and needs `duplex = 0`: the
stock `duplex = 2` (a repeater) sends everything its receiver hears straight
back out its transmitter, so the controller would transmit its users twice.
"""
from __future__ import annotations

import math
import socket
import sys
import struct
import threading
import time

HEADER = struct.Struct(">4s7I")
SAMPLES = 160
NODE_1999_RX = ("127.0.0.1", 32001)
NODE_1998_RX = ("127.0.0.1", 32002)


def voice(seq: int, pcm: bytes) -> bytes:
    return HEADER.pack(b"USRP", seq, 0, 1, 0, 0, 0, 0) + pcm


def tone_frames(hz: float, seconds: float) -> list[bytes]:
    frames = []
    for n in range(int(seconds * 8000 / SAMPLES)):
        samples = [
            int(8000 * math.sin(2 * math.pi * hz * (n * SAMPLES + i) / 8000)) for i in range(SAMPLES)
        ]
        frames.append(struct.pack(f"<{SAMPLES}h", *samples))
    return frames


def main() -> None:
    to_1999 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    to_1999.bind(("127.0.0.1", 34001))
    to_1999.settimeout(0.5)
    from_1998 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    from_1998.bind(("127.0.0.1", 34002))
    from_1998.settimeout(0.5)
    heard: list[tuple[float, int, list[int]]] = []
    echoed: list[tuple[float, int, list[int]]] = []  # 1999 transmitting what it received
    stop = threading.Event()

    def listen(sock: socket.socket, into: list) -> None:
        while not stop.is_set():
            try:
                data = sock.recv(1024)
            except socket.timeout:
                continue
            eye, _seq, _memory, keyup, *_ = HEADER.unpack(data[: HEADER.size])
            assert eye == b"USRP"
            pcm = data[HEADER.size :]
            into.append((time.monotonic(), keyup, list(struct.unpack(f"<{len(pcm) // 2}h", pcm))))

    listeners = [threading.Thread(target=listen, args=(from_1998, heard)), threading.Thread(target=listen, args=(to_1999, echoed))]
    for listener in listeners:
        listener.start()
    time.sleep(1)
    heard.clear()  # telemetry from before the test
    echoed.clear()

    reverse = sys.argv[1:] == ["reverse"]  # into 1998, out of 1999: the link's audio to us
    if reverse:
        heard, echoed = echoed, heard
    sender, target = (from_1998, NODE_1998_RX) if reverse else (to_1999, NODE_1999_RX)
    start = time.monotonic()
    for seq, pcm in enumerate(tone_frames(1000, 2.0), 1):
        sender.sendto(voice(seq, pcm), target)
        time.sleep(max(0.0, start + seq * 0.02 - time.monotonic()))
    time.sleep(15)  # through the node's hang time and courtesy tone
    stop.set()
    for listener in listeners:
        listener.join()

    keyed = [samples for _t, keyup, samples in heard if keyup and samples]
    unkeys = [t - start for t, keyup, samples in heard if not keyup and not samples]
    loud = [samples for samples in keyed if max(map(abs, samples)) > 4000]
    audio = [s for samples in loud for s in samples]
    crossings = sum(1 for a, b in zip(audio, audio[1:]) if a < 0 <= b)
    to_node, from_node = ("1998", "1999") if reverse else ("1999", "1998")
    print(f"voice packets from {from_node} with keyup: {len(keyed)} ({len(keyed) * 0.02:.2f}s)")
    print(f"unkey packets: {len(unkeys)} at {[round(t, 2) for t in unkeys]}s after the tone started")
    if audio:
        print(f"loud packets: {len(loud)} ({len(loud) * 0.02:.2f}s), about {crossings / (len(audio) / 8000):.0f} Hz, peak {max(map(abs, audio))}")
    echo = [samples for _t, keyup, samples in echoed if keyup and samples and max(map(abs, samples)) > 4000]
    print(f"{to_node} echoed {len(echo)} loud packets of its own receiver back ({len(echoed)} packets in all)")


if __name__ == "__main__":
    main()
