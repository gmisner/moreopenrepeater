"""One-off spike: verify our link.audiosocket framing against real Asterisk.

Not part of the test suite -- run manually to prove wire-level interop
before writing more link code. Confirmed working 2026-09-26 against
AllStarLink ASL3 (asl3-asterisk 2:22.10.1+asl3-3.10.5-1.deb12) on a Debian
12 arm64 Lima VM.

Proactively sends periodic silence frames rather than only reacting to
received audio -- app_audiosocket.c tears the call down after 2s of no
activity on the socket in EITHER direction, and our test call's Local
channel has no real audio source, so Asterisk itself never sends anything.
This mirrors how the real audio_io pipeline will behave (always producing
blocks, silence or not) and confirms that satisfies the keep-alive.

To reproduce:

    # 1. Create/start the VM (arm64-native on Apple Silicon; must match
    #    ASL3's packaged target -- the "bookworm" apt repo pulls in
    #    bookworm-specific lib versions that conflict on a trixie/Debian 13
    #    base) and install ASL3:
    limactl start --name=asl3 template:debian-12
    limactl shell asl3 -- sudo bash -c '
        wget -q https://repo.allstarlink.org/public/asl-apt-repos.deb12_all.deb -O /tmp/r.deb &&
        dpkg -i /tmp/r.deb && apt-get update &&
        apt-get install -y asl3 asl3-menu'

    # 2. Load the AudioSocket modules (not autoloaded by default) and open
    #    the AMI port to non-localhost:
    limactl shell asl3 -- sudo asterisk -rx "module load res_audiosocket.so"
    limactl shell asl3 -- sudo asterisk -rx "module load app_audiosocket.so"
    limactl shell asl3 -- sudo asterisk -rx "module load chan_audiosocket.so"
    limactl shell asl3 -- sudo sed -i 's/^bindaddr = 127.0.0.1.*/bindaddr = 0.0.0.0/' \
        /etc/asterisk/manager.conf
    limactl shell asl3 -- sudo asterisk -rx "manager reload"

    # 3. Add a minimal test extension (custom/extensions.conf is
    #    auto-#tryinclude'd by the packaged extensions.conf):
    #    [default]
    #    exten => 1000,1,Answer()
    #     same => n,AudioSocket(11111111-1111-1111-1111-111111111111,127.0.0.1:18090)
    #     same => n,Hangup()
    limactl shell asl3 -- sudo asterisk -rx "dialplan reload"

    # 4. Tunnel AMI in (-L) and AudioSocket's outbound connection back out
    #    (-R) through a *dedicated* control socket -- do NOT reuse Lima's own
    #    shared ControlMaster (`limactl shell`'s ssh.sock), and start this
    #    script (which binds the host port) *before* opening the -R tunnel:
    #    Lima's hostagent auto-mirrors any new guest-side listening socket
    #    (which is exactly what -R creates) back out to the host on the same
    #    port number, and will win the bind race and steal the port if it
    #    gets there first.
    python3 spikes/audiosocket_asl3_spike.py &
    ssh -F ~/.lima/asl3/ssh.config -o ControlPath=/tmp/asl3-tunnel.sock -fN \
        -L 5038:127.0.0.1:5038 -R 18090:127.0.0.1:18090 lima-asl3

    # 5. Originate the test call via AMI (Originate action; there's no
    #    "originate" Asterisk CLI command in this build) -- see
    #    link.ami_client.AMIClient, or asterisk -rx with an AMI action isn't
    #    a thing, so use the AMIClient or another AMI tool.
"""
import socket
import threading
import time

from link.audiosocket import FrameType, encode_frame, read_frame

SILENCE_FRAME = encode_frame(FrameType.AUDIO, bytes(320))  # 20ms of 8kHz 16-bit silence


def sender(conn: socket.socket, stop: threading.Event) -> None:
    while not stop.is_set():
        conn.sendall(SILENCE_FRAME)
        time.sleep(0.02)


def handle(conn: socket.socket) -> None:
    print("connection accepted")
    frame = read_frame(conn.recv)
    print("first frame:", frame.type, frame.payload.hex())
    assert frame.type == FrameType.UUID

    stop = threading.Event()
    send_thread = threading.Thread(target=sender, args=(conn, stop), daemon=True)
    send_thread.start()

    conn.settimeout(0.5)
    deadline = time.monotonic() + 5.0
    frame_count = 0
    try:
        while time.monotonic() < deadline:
            try:
                frame = read_frame(conn.recv)
            except socket.timeout:
                continue  # Asterisk's side may have no real audio source to send
            frame_count += 1
            if frame_count <= 5 or frame.type != FrameType.AUDIO:
                print("received frame:", frame.type, len(frame.payload), "bytes")
            if frame.type == FrameType.HANGUP:
                break
    finally:
        stop.set()
        send_thread.join(timeout=1)

    print(f"survived 5s without a timeout, received {frame_count} frames total -- sending our own HANGUP")
    conn.sendall(encode_frame(FrameType.HANGUP))
    conn.close()
    print("done")


server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", 18090))
server.listen(1)
print("listening on 127.0.0.1:18090, waiting for Asterisk to connect...")

conn, addr = server.accept()
handle(conn)
server.close()
