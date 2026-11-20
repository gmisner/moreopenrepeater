"""A fake NTP server on a real UDP socket on 127.0.0.1, for the SNTP client's tests. It answers the way a test tells it to."""
import socket
import struct
import threading
import time

NTP_EPOCH = 2208988800


def stamp(unix_time):
    whole = int(unix_time)
    return (whole + NTP_EPOCH) & 0xFFFFFFFF, int((unix_time - whole) * 2 ** 32) & 0xFFFFFFFF


def packet(*, leap=0, version=4, mode=4, stratum=2, refid=0, originate=(0, 0), receive=None, transmit=None):
    """A 48-byte NTP packet. `receive` and `transmit` are Unix times (or an (s, f) pair, or None for zeros)."""

    def pair(value):
        if value is None:
            return (0, 0)
        return value if isinstance(value, tuple) else stamp(value)

    r, x = pair(receive), pair(transmit)
    return struct.pack("!4B11I", (leap << 6) | (version << 3) | mode, stratum, 6, 0, 0, 0, refid, 0, 0, *originate, *r, *x)


def request_transmit(data):
    """The transmit timestamp the client sent: what a server must echo back as the originate timestamp."""
    return struct.unpack("!4B11I", data[:48])[13:15]


def good_reply(data, *, offset=0.0, stratum=2, **fields):
    """What an honest server whose clock is `offset` seconds ahead of the real one answers."""
    t2 = time.time() + offset
    return packet(stratum=stratum, originate=request_transmit(data), receive=t2, transmit=t2 + 0.0001, **fields)


class FakeNtp:
    """`behaviour(request, sock, address)` is called for every datagram and sends what it wants; the default is a good reply."""

    def __init__(self, behaviour=None):
        self.behaviour = behaviour or (lambda data, sock, address: sock.sendto(good_reply(data), address))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self.requests = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                data, address = self.sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                return
            self.requests.append(data)
            self.behaviour(data, self.sock, address)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(2)
        self.sock.close()
