"""Raspberry Pi header GPIO pins for PTT and COS.

Uses the Linux GPIO character device (/dev/gpiochipN, uAPI v2) through
ioctls, like `cm108` uses raw hidraw -- no extra packages. Pins are BCM
GPIO numbers (GPIO17 is header pin 11), which are the line offsets on the
chip labeled "pinctrl-..." (bcm2835 on a Pi 3, bcm2711 on a Pi 4, rp1 on a
Pi 5). Its /dev/gpiochipN is group `gpio` on Raspberry Pi OS.

The kernel applies active-low polarity, so `read`/`write` deal in "active",
not voltage. Inputs get the Pi's internal pull resistor toward the inactive
level (pull-up for active low), so an open-collector COS output needs no
resistor of its own. The pins are 3.3 V only -- never connect 5 V logic.
"""
from __future__ import annotations

import fcntl
import glob
import os
import struct
from typing import Protocol

_FLAG_ACTIVE_LOW = 1 << 1
_FLAG_INPUT = 1 << 2
_FLAG_OUTPUT = 1 << 3
_FLAG_BIAS_PULL_UP = 1 << 8
_FLAG_BIAS_PULL_DOWN = 1 << 9

# struct gpio_v2_line_request: offsets[64], consumer[32], a line config
# (flags, num_attrs, padding[5], attrs[10]), num_lines, event_buffer_size,
# padding[5], fd.
_LINE_REQUEST = struct.Struct("=64I32sQI5I240xII5Ii")
_LINE_VALUES = struct.Struct("=QQ")  # bits, mask
_CHIP_INFO_SIZE = 68  # name[32], label[32], lines


def _ioc(direction: int, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (0xB4 << 8) | number


_GET_CHIPINFO = _ioc(2, 0x01, _CHIP_INFO_SIZE)
_GET_LINE = _ioc(3, 0x07, _LINE_REQUEST.size)
_GET_VALUES = _ioc(3, 0x0E, _LINE_VALUES.size)
_SET_VALUES = _ioc(3, 0x0F, _LINE_VALUES.size)


def line_request(pin: int, flags: int, consumer: str = "moreopenrepeater") -> bytearray:
    offsets = [pin] + [0] * 63
    return bytearray(_LINE_REQUEST.pack(*offsets, consumer.encode()[:31], flags, 0, *[0] * 5, 1, 0, *[0] * 5, 0))


def line_flags(output: bool, active_low: bool) -> int:
    flags = _FLAG_ACTIVE_LOW if active_low else 0
    if output:
        return flags | _FLAG_OUTPUT
    return flags | _FLAG_INPUT | (_FLAG_BIAS_PULL_UP if active_low else _FLAG_BIAS_PULL_DOWN)


def find_header_chip() -> str:
    """The /dev/gpiochipN wired to the 40-pin header."""
    for path in sorted(glob.glob("/dev/gpiochip*")):
        if os.path.islink(path):
            continue
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            continue
        try:
            info = bytearray(_CHIP_INFO_SIZE)
            fcntl.ioctl(fd, _GET_CHIPINFO, info, True)
        except OSError:
            continue
        finally:
            os.close(fd)
        if info[32:64].rstrip(b"\0").startswith(b"pinctrl-"):
            return path
    return "/dev/gpiochip0"


class GpioLine(Protocol):
    def read(self) -> bool: ...
    def write(self, active: bool) -> None: ...
    def close(self) -> None: ...


class LinuxGpioLine:
    """One requested header pin; an output starts inactive."""

    def __init__(self, chip: str, pin: int, *, output: bool, active_low: bool) -> None:
        request = line_request(pin, line_flags(output, active_low))
        chip_fd = os.open(chip, os.O_RDWR)
        try:
            fcntl.ioctl(chip_fd, _GET_LINE, request, True)
        finally:
            os.close(chip_fd)
        self._fd = _LINE_REQUEST.unpack(request)[-1]

    def read(self) -> bool:
        values = bytearray(_LINE_VALUES.pack(0, 1))
        fcntl.ioctl(self._fd, _GET_VALUES, values, True)
        return bool(_LINE_VALUES.unpack(values)[0] & 1)

    def write(self, active: bool) -> None:
        fcntl.ioctl(self._fd, _SET_VALUES, bytearray(_LINE_VALUES.pack(int(active), 1)), True)

    def close(self) -> None:
        os.close(self._fd)


def open_header_pin(pin: int, *, output: bool, active_low: bool) -> LinuxGpioLine:
    return LinuxGpioLine(find_header_chip(), pin, output=output, active_low=active_low)
