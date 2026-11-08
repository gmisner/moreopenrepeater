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

import errno
import fcntl
import logging
import os
import threading
from pathlib import Path
from typing import Callable, Optional, Protocol

GPIO1 = 0x01
GPIO2 = 0x02
GPIO3 = 0x04
GPIO4 = 0x08

VOL_UP = 0x01
VOL_DN = 0x02

INPUT_REPORT_LENGTH = 4

# USB unplug/reset: the hidraw node is gone, the open fd's device vanished,
# or the pipe to it broke mid-write.
_GONE_ERRNOS = frozenset({errno.ENODEV, errno.ENOENT, errno.EPIPE})

_logger = logging.getLogger("moreopenrepeater.cm108")


def gpio_bit(pin: int) -> int:
    """GPIO1-8 as a bit of the data/mask/input bytes."""
    if not 1 <= pin <= 8:
        raise ValueError(f"no GPIO{pin} on a CM108-family chip")
    return 1 << (pin - 1)


def _hidraw_stable_id(node: Path, vendor: int, product: int) -> str:
    """Identity that survives hidrawN renumbering: serial when present, else bus path."""
    try:
        device = (node / "device").resolve()
    except OSError:
        device = node / "device"
    for path in (device, *getattr(device, "parents", ())):
        serial = ""
        try:
            serial = (path / "serial").read_text().strip()
        except OSError:
            pass
        if serial and serial not in ("0", "n/a", "N/A"):
            return f"usb-{vendor:04x}:{product:04x}-{serial}"
        try:
            busnum = (path / "busnum").read_text().strip()
            devpath = (path / "devpath").read_text().strip()
        except OSError:
            continue
        if busnum and devpath:
            return f"usb-{vendor:04x}:{product:04x}-{busnum}-{devpath}"
    return f"usb-{vendor:04x}:{product:04x}"


def find_interfaces(sysfs: Path = Path("/sys/class/hidraw")) -> list[dict]:
    """CM108-family hidraw nodes: path, vendor, product, name, stable_id.
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
            found.append(
                {
                    "path": f"/dev/{node.name}",
                    "vendor": vendor,
                    "product": product,
                    "name": fields.get("HID_NAME", ""),
                    "stable_id": _hidraw_stable_id(node, vendor, product),
                }
            )
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
        if os.write(self._fd, report) != len(report):
            raise OSError(errno.EIO, "Incomplete CM108 output report")

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

    After a USB drop (ENODEV/EPIPE), the next PTT/COS I/O closes the dead
    fd, finds the interface again by USB identity (hidrawN can change),
    opens it, and restores spare GPIO outputs with PTT released. `ensure_open` does the same without waiting for PTT/COS.
    """

    def __init__(
        self,
        device: HidrawDevice,
        ptt_pin: int = 3,
        cos_input: int = VOL_DN,
        cos_active_low: bool = True,
        find: Callable[[], list[dict]] = find_interfaces,
        open_device: Callable[[str], HidrawDevice] = LinuxHidrawDevice,
        stable_id: Optional[str] = None,
    ) -> None:
        self.device = device
        self.ptt_pin = ptt_pin
        self.cos_input = cos_input
        self.cos_active_low = cos_active_low
        self._find = find
        self._open_device = open_device
        self._preferred_path = getattr(device, "path", None)
        self._preferred_id = stable_id or self._identity_for_path(self._preferred_path)
        self._outputs = 0  # mask
        self._levels = 0  # data
        self._lock = threading.Lock()
        self.last_inputs: Optional[tuple[int, int]] = None  # (buttons, GPIO levels)
        self._gone = False  # last I/O found no interface; next I/O still retries
        self._device_closed = False

    def _identity_for_path(self, path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        for found in self._find():
            if found.get("path") == path:
                return found.get("stable_id")
        return None

    def _report(self) -> bytes:
        return bytes([0x00, 0x00, self._levels, self._outputs, 0x00])

    def _close_device(self) -> None:
        if self._device_closed:
            return
        self._device_closed = True
        close = getattr(self.device, "close", None)
        if close is None:
            return
        try:
            close()
        except OSError:
            pass

    def _candidate_paths(self) -> list[str]:
        """Stay with the same physical interface when its identity is known."""
        found = self._find()
        if self._preferred_id:
            paths = [entry["path"] for entry in found if entry.get("stable_id") == self._preferred_id]
            return sorted(paths, key=lambda path: path != self._preferred_path)
        paths = ([self._preferred_path] if self._preferred_path else []) + [entry["path"] for entry in found]
        return list(dict.fromkeys(paths))

    def _reopen(self) -> bool:
        """Close the dead fd and open a CM108 by preferred path or USB ID."""
        self._close_device()
        self._levels &= ~gpio_bit(self.ptt_pin)
        for path in self._candidate_paths():
            try:
                device = self._open_device(path)
            except OSError as error:
                _logger.debug("couldn't reopen CM108 at %s: %s", path, error)
                continue
            try:
                device.write_report(self._report())
            except OSError as error:
                close = getattr(device, "close", None)
                if close is not None:
                    try:
                        close()
                    except OSError:
                        pass
                if error.errno in _GONE_ERRNOS:
                    _logger.debug("reopened %s but it is already gone: %s", path, error)
                    continue
                raise
            self.device = device
            self._device_closed = False
            self._preferred_path = path
            self._preferred_id = self._identity_for_path(path) or self._preferred_id
            self._gone = False
            _logger.info("reopened CM108 at %s after USB drop", path)
            return True
        if not self._gone:
            path = self._preferred_path or "CM108"
            _logger.warning("%s is gone; GPIO writes ignored until it reappears", path)
            self._gone = True
        return False

    def ensure_open(self) -> bool:
        """Reopen now if the interface was lost, without waiting for PTT/COS."""
        with self._lock:
            if not self._device_closed and self._preferred_path and os.path.exists(self._preferred_path):
                try:
                    self.device.get_input_report(INPUT_REPORT_LENGTH)
                    return True
                except OSError as error:
                    if error.errno not in _GONE_ERRNOS:
                        raise
            return self._reopen()

    @property
    def gone(self) -> bool:
        return self._gone

    @property
    def path(self) -> Optional[str]:
        return getattr(self.device, "path", None) or self._preferred_path

    def _write(self) -> None:
        if self._device_closed:
            self._reopen()
            return
        try:
            self.device.write_report(self._report())
            self._gone = False
            return
        except OSError as error:
            if error.errno not in _GONE_ERRNOS:
                raise
        self._reopen()  # swallows failure so unkey/cleanup doesn't explode on a dead fd

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
            if self._device_closed and not self._reopen():
                raise OSError(errno.ENODEV, "CM108 is unavailable")
            try:
                report = self.device.get_input_report(INPUT_REPORT_LENGTH)
                self._gone = False
            except OSError as error:
                if error.errno not in _GONE_ERRNOS:
                    raise
                if not self._reopen():
                    raise
                report = self.device.get_input_report(INPUT_REPORT_LENGTH)
        self.last_inputs = (report[0], report[1])
        return self.last_inputs

    def read_gpio(self, pin: int) -> bool:
        return bool(self.read_inputs()[1] & gpio_bit(pin))

    def set_ptt(self, active: bool) -> None:
        bit = gpio_bit(self.ptt_pin)
        with self._lock:
            self._outputs |= bit
            self._levels = self._levels | bit if active else self._levels & ~bit
            if not self._device_closed:
                try:
                    self.device.write_report(self._report())
                    self._gone = False
                    return
                except OSError as error:
                    # Reopening or writing a spare pin must never replay a failed key.
                    self._levels &= ~bit
                    if error.errno not in _GONE_ERRNOS:
                        raise
            self._levels &= ~bit
            reopened = self._reopen()
            if active or not reopened:
                raise OSError(errno.ENODEV, "CM108 disconnected during PTT write")

    def cos_from_buttons(self, buttons: int) -> bool:
        pulled_low = bool(buttons & self.cos_input)
        return pulled_low if self.cos_active_low else not pulled_low

    def read_cos(self) -> bool:
        return self.cos_from_buttons(self.read_inputs()[0])
