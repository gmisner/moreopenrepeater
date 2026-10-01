"""CM108/CM119-family USB sound-card GPIO driver: PTT, COS and spare pins.

These chips are the common basis for cheap ham radio USB interface dongles
(URI/RIM/DMK-style), where PTT (transmitter key, an output) and COS/COR
(carrier-operated squelch, an input) ride on the sound card's spare GPIO
pins and button inputs alongside the radio audio.

Protocol as used by AllStarLink's chan_simpleusb/res_usbradio (SET_REPORT /
GET_REPORT on the HID interface) and direwolf's cm108.c (hidraw write):

- Output report, 4 bytes: [0x00, data, mask, 0x00]. `data` sets GPIO1-8's
  levels and `mask` marks which pins are outputs; a pin whose mask bit is 0
  reverts to an input on that same write -- the chip has no "leave other
  pins alone" mode, so every write carries every output. On hidraw it's
  written with a leading report-ID byte of 0 (5 bytes).
- Input report, 4 bytes: byte 0 holds the button inputs (VOL_UP, VOL_DN,
  HOOK on a CM108AH, ...), byte 1 GPIO1-8's levels. It's read on demand
  (HIDIOCGINPUT, a GET_REPORT), like chan_simpleusb polls it. A button's
  bit is set while its pin is pulled low (pressed); the pins have pull-ups.
- On URI/RIM/DMK-style interfaces GPIO3 is PTT and COS comes in on VOL_DN,
  usually active low: chan_simpleusb's `carrierfrom=usbinvert` ("active
  low") reports carrier while the VOL_DN bit is set. GPIO5-8 exist on
  CM119-family chips only, and on a CM108AH GPIO2 isn't a real GPIO.
"""
from __future__ import annotations

import fcntl
import os
import threading
from pathlib import Path
from typing import Optional, Protocol

GPIO1 = 0x01
GPIO2 = 0x02
GPIO3 = 0x04
GPIO4 = 0x08

VOL_UP = 0x01
VOL_DN = 0x02

INPUT_REPORT_LENGTH = 4


def gpio_bit(pin: int) -> int:
    """GPIO1-8 as a bit of the data/mask/input bytes."""
    if not 1 <= pin <= 8:
        raise ValueError(f"no GPIO{pin} on a CM108-family chip")
    return 1 << (pin - 1)


def find_interfaces(sysfs: Path = Path("/sys/class/hidraw")) -> list[dict]:
    """CM108-family hidraw nodes: [{"path", "vendor", "product", "name"}].
    The N in /dev/hidrawN depends on USB enumeration order, so look it up
    rather than configuring it."""
    found = []
    for node in sorted(sysfs.glob("hidraw*")):
        try:
            uevent = (node / "device" / "uevent").read_text()
        except OSError:
            continue
        fields = dict(line.split("=", 1) for line in uevent.splitlines() if "=" in line)
        try:
            _bus, vendor, product = (int(part, 16) for part in fields.get("HID_ID", "").split(":"))
        except ValueError:
            continue
        if vendor == 0x0D8C or (vendor, product) == (0x1209, 0x7388):
            found.append({"path": f"/dev/{node.name}", "vendor": vendor, "product": product, "name": fields.get("HID_NAME", "")})
    return found


def _hidiocginput(length: int) -> int:
    # _IOC(_IOC_READ | _IOC_WRITE, 'H', 0x0A, length), Linux 5.11+
    return (3 << 30) | (length << 16) | (ord("H") << 8) | 0x0A


class HidrawDevice(Protocol):
    def write_report(self, report: bytes) -> None: ...
    def get_input_report(self, length: int) -> bytes: ...


class LinuxHidrawDevice:
    """A real /dev/hidrawN device node."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._fd = os.open(path, os.O_RDWR)

    def write_report(self, report: bytes) -> None:
        os.write(self._fd, report)

    def get_input_report(self, length: int) -> bytes:
        buffer = bytearray(length + 1)  # report ID 0, then the report
        fcntl.ioctl(self._fd, _hidiocginput(len(buffer)), buffer, True)
        return bytes(buffer[1:])

    def close(self) -> None:
        os.close(self._fd)


class CM108Interface:
    """PTT, COS and the spare GPIO pins of one CM108-family interface.

    Called from the audio worker (PTT), the COS poller and the event loop
    (spare pins), so every access holds a lock and every write carries all
    the pins that are outputs.
    """

    def __init__(self, device: HidrawDevice, ptt_pin: int = 3, cos_input: int = VOL_DN, cos_active_low: bool = True) -> None:
        self.device = device
        self.ptt_pin = ptt_pin
        self.cos_input = cos_input
        self.cos_active_low = cos_active_low
        self._outputs = 0  # mask
        self._levels = 0  # data
        self._lock = threading.Lock()
        self.last_inputs: Optional[tuple[int, int]] = None  # (buttons, GPIO levels)

    def _write(self) -> None:
        self.device.write_report(bytes([0x00, 0x00, self._levels, self._outputs, 0x00]))

    def set_gpio(self, pin: int, active: bool) -> None:
        """Make GPIO<pin> an output and drive it high (`active`) or low."""
        bit = gpio_bit(pin)
        with self._lock:
            self._outputs |= bit
            self._levels = self._levels | bit if active else self._levels & ~bit
            self._write()

    def release_gpio(self, pin: int) -> None:
        """Turn GPIO<pin> back into an input."""
        bit = gpio_bit(pin)
        with self._lock:
            if not self._outputs & bit:
                return
            self._outputs &= ~bit
            self._levels &= ~bit
            self._write()

    def output_level(self, pin: int) -> Optional[bool]:
        """What an output pin is driven to, or None for an input."""
        bit = gpio_bit(pin)
        with self._lock:
            return bool(self._levels & bit) if self._outputs & bit else None

    def read_inputs(self) -> tuple[int, int]:
        """(button inputs, GPIO levels), read from the chip now."""
        with self._lock:
            report = self.device.get_input_report(INPUT_REPORT_LENGTH)
        self.last_inputs = (report[0], report[1])
        return self.last_inputs

    def read_gpio(self, pin: int) -> bool:
        return bool(self.read_inputs()[1] & gpio_bit(pin))

    def set_ptt(self, active: bool) -> None:
        self.set_gpio(self.ptt_pin, active)

    def cos_from_buttons(self, buttons: int) -> bool:
        pulled_low = bool(buttons & self.cos_input)
        return pulled_low if self.cos_active_low else not pulled_low

    def read_cos(self) -> bool:
        return self.cos_from_buttons(self.read_inputs()[0])
