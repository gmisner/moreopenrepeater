"""Serial-port modem lines for PTT and COS.

Many radio interfaces (USB-serial adapters, RigBlaster-style boxes, an
AIOC's virtual COM port) key the transmitter with RTS or DTR and report
carrier on CTS, DSR or DCD. These are the tty's modem-control lines, set
and read with the TIOCMBIS/TIOCMBIC/TIOCMGET ioctls -- no extra packages.
The device nodes are group `dialout`.

"Active high" means the line is asserted (on): positive voltage on a real
RS-232 port, low on most 3.3 V/5 V TTL adapters, which invert. `read` and
`write` deal in "active", like `pi_gpio`.

Opening a tty raises DTR and RTS, so an output is set inactive straight
after opening. When the last process holding a port closes it (including
a crash), the kernel drops DTR and RTS if HUPCL is set: right for active
high, where dropped means unkeyed. For active low, dropped would mean
keyed, so HUPCL is cleared and the lines are left as they were.
"""
from __future__ import annotations

import fcntl
import glob
import os
import struct
import termios
from typing import Protocol

OUTPUT_LINES = {"rts": termios.TIOCM_RTS, "dtr": termios.TIOCM_DTR}
INPUT_LINES = {"cts": termios.TIOCM_CTS, "dsr": termios.TIOCM_DSR, "dcd": termios.TIOCM_CAR}
_INT = struct.Struct("i")


class ModemPort(Protocol):
    def get(self) -> int: ...
    def assert_bits(self, bits: int) -> None: ...
    def clear_bits(self, bits: int) -> None: ...
    def set_hangup_on_close(self, on: bool) -> None: ...
    def close(self) -> None: ...


class LinuxSerialPort:
    """A tty opened only for its modem lines: no data is read or written."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)

    def get(self) -> int:
        return _INT.unpack(fcntl.ioctl(self._fd, termios.TIOCMGET, _INT.pack(0)))[0]

    def assert_bits(self, bits: int) -> None:
        fcntl.ioctl(self._fd, termios.TIOCMBIS, _INT.pack(bits))

    def clear_bits(self, bits: int) -> None:
        fcntl.ioctl(self._fd, termios.TIOCMBIC, _INT.pack(bits))

    def set_hangup_on_close(self, on: bool) -> None:
        attrs = termios.tcgetattr(self._fd)
        attrs[2] = attrs[2] | termios.HUPCL if on else attrs[2] & ~termios.HUPCL
        termios.tcsetattr(self._fd, termios.TCSANOW, attrs)

    def close(self) -> None:
        os.close(self._fd)


class SerialLine:
    """One modem line of a port, as a `pi_gpio.GpioLine`."""

    def __init__(self, port: ModemPort, line: str, *, output: bool, active_low: bool) -> None:
        lines = OUTPUT_LINES if output else INPUT_LINES
        if line not in lines:
            raise ValueError(f"{line.upper()} isn't a serial {'output' if output else 'input'}")
        self.port = port
        self.bit = lines[line]
        self.active_low = active_low
        if output:
            port.set_hangup_on_close(not active_low)
            self.write(False)

    def read(self) -> bool:
        return bool(self.port.get() & self.bit) != self.active_low

    def write(self, active: bool) -> None:
        if active != self.active_low:
            self.port.assert_bits(self.bit)
        else:
            self.port.clear_bits(self.bit)

    def close(self) -> None:
        self.port.close()


def open_serial_line(device: str, line: str, *, output: bool, active_low: bool) -> SerialLine:
    port = LinuxSerialPort(device)
    try:
        return SerialLine(port, line, output=output, active_low=active_low)
    except termios.error as error:
        port.close()
        raise OSError(*error.args) from error
    except (OSError, ValueError):
        port.close()
        raise


def find_serial_ports() -> list[dict]:
    """Serial devices to offer: [{"path", "target"}]. The /dev/serial/by-id
    names come first, since ttyUSBn numbers depend on plug-in order."""
    found = []
    for path in sorted(glob.glob("/dev/serial/by-id/*")):
        found.append({"path": path, "target": os.path.realpath(path)})
    linked = {entry["target"] for entry in found}
    for path in sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")):
        if path not in linked:
            found.append({"path": path, "target": path})
    return found
