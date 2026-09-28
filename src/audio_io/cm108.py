"""CM108/CM119-family USB sound-card GPIO driver for PTT output and COS input.

These chips are the common basis for cheap ham radio USB interface dongles
(URI/RIM/DMK-style), where PTT (transmitter key, an output) and COS/COR
(carrier-operated squelch, an input) ride on the sound card's spare GPIO
pins alongside the radio audio.

Wire protocol confirmed against real open-source drivers -- direwolf's
cm108.c (post issue #210's byte-order fix), SvxLink's PttHidraw.cpp /
SquelchHidraw.cpp, and AllStarLink's uridiag.c:

- Despite the CM108 datasheet calling this a "Feature" report, none of
  these drivers use the HIDIOCSFEATURE/HIDIOCGFEATURE ioctls -- they do
  plain 5-byte write()/read() on the /dev/hidrawN device node.
- Write format is [0x00, 0x00, data, mask, 0x00]: a report-ID byte, a
  reserved byte, the GPIO output *data* byte, the GPIO *mask* byte (bit=1
  marks that pin as an output and applies data's bit to it -- every other
  pin's mask bit is 0, which reverts it to input on that same write; the
  chip has no "leave other pins alone" mode), and a trailing reserved byte.
  Writing only 4 bytes fails with EPIPE; some historical code (and an old
  direwolf version) had data/mask reversed -- that ordering is wrong.
- Read format is a 5-byte read() where only byte 0 carries live GPIO pin
  levels.
- De facto pin convention on URI/RIM/DMK-style interfaces: GPIO3 (bit
  0x04) = PTT output, GPIO4 (bit 0x08) = COS/COR input.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

GPIO1 = 0x01
GPIO2 = 0x02
GPIO3 = 0x04
GPIO4 = 0x08


class HidrawDevice(Protocol):
    def write_report(self, report: bytes) -> None: ...
    def read_report(self, length: int) -> bytes: ...


class LinuxHidrawDevice:
    """A real /dev/hidrawN device node."""

    def __init__(self, path: str) -> None:
        self._fd = os.open(path, os.O_RDWR)

    def write_report(self, report: bytes) -> None:
        os.write(self._fd, report)

    def read_report(self, length: int) -> bytes:
        return os.read(self._fd, length)

    def close(self) -> None:
        os.close(self._fd)


@dataclass
class CM108Interface:
    """GPIO access over a CM108-family USB sound card, plus PTT/COS convenience
    wrappers for the two pins those roles conventionally use.

    CM108-family chips have 4 (CM108) or up to 8 (CM109/CM119) GPIO pins
    total; URI/RIM/DMK-style interfaces only use two of them (PTT, COS), but
    the remaining pins are real GPIO usable for other purposes (e.g. an
    external alarm input, a relay output) via `set_gpio`/`read_gpio` directly
    -- same wire protocol, just a different pin bit.
    """

    device: HidrawDevice
    ptt_pin: int = GPIO3
    cos_pin: int = GPIO4

    def set_gpio(self, pin: int, active: bool) -> None:
        data = pin if active else 0x00
        mask = pin
        self.device.write_report(bytes([0x00, 0x00, data, mask, 0x00]))

    def read_gpio(self, pin: int) -> bool:
        report = self.device.read_report(5)
        return bool(report[0] & pin)

    def set_ptt(self, active: bool) -> None:
        self.set_gpio(self.ptt_pin, active)

    def read_cos(self) -> bool:
        return self.read_gpio(self.cos_pin)
