"""What the core can reach on the host, and plain words for when it can't (#221).

In the container install the core is only given the devices `docker/setup.sh` was asked for: the sound cards and the
PTT interface always, and the Pi's GPIO chip, serial adapters and a second (link radio) interface only with
`./setup.sh --gpio`, `--serial DEVICE` and `--link-radio`. A page that needs one of them and doesn't have it used to
show "No such file or directory". These functions say what is missing and the command that gives it to the core.

They only change the wording when the core is in a container (or the owner's setting says the hardware is off), so
the native install's messages stay as they were.
"""
from __future__ import annotations

import errno
import glob
import os
from typing import Callable, Optional

from audio_io.serial_lines import find_serial_ports

NO_SECOND_INTERFACE = "The linked radio is set to use a CM108, but no second CM108 interface was found."


def in_container(env: Optional[dict] = None, exists: Callable[[str], bool] = os.path.exists) -> bool:
    env = os.environ if env is None else env
    return env.get("MOREOPENREPEATER_INSTALL") == "container" or exists("/.dockerenv") or exists("/run/.containerenv")


def _gpio_chips() -> list[str]:
    return glob.glob("/dev/gpiochip*")


def _reason(error: OSError) -> str:
    return error.strerror or str(error)


def header_pin_reason(error: OSError) -> str:
    """Why a Pi header pin couldn't be opened, for the end of "couldn't open GPIO17 for PTT: ...".

    Without the chip device at all, in a container, that is `setup.sh --gpio` not having been run. With the chip but
    no permission, the container's gpio group number is not the host's (`--gpio` also sets that)."""
    if not in_container():
        return _reason(error)
    chips = _gpio_chips()
    if not chips and error.errno in (errno.ENOENT, errno.ENODEV):
        return (
            "the Pi's GPIO chip is not given to the core container. On the Pi, run ./setup.sh --gpio in the docker "
            "folder (it adds the chip and restarts the core)."
        )
    if chips and error.errno in (errno.EACCES, errno.EPERM):
        return (
            "the core isn't allowed to use the Pi's GPIO chip: the container's gpio group number is not the host's. "
            "Run ./setup.sh --gpio in the docker folder; it sets the number."
        )
    return _reason(error)


def serial_port_reason(error: OSError) -> str:
    """Why a serial port couldn't be opened, for the end of "couldn't open /dev/ttyUSB0 (RTS) for PTT: ..."."""
    if in_container() and error.errno in (errno.ENOENT, errno.ENODEV, errno.ENXIO):
        return (
            "that serial port is not given to the core container. Plug the adapter in and run "
            "./setup.sh --serial /dev/serial/by-id/NAME in the docker folder (ls /dev/serial/by-id on the Pi shows "
            "the names)."
        )
    return _reason(error)


def missing_link_interface(env: Optional[dict] = None) -> str:
    """The link radio wants a CM108 and the core has none to give it: say why, in the words of the setup."""
    env = os.environ if env is None else env
    setting = env.get("MOREOPENREPEATER_LINK_CM108_HIDRAW", "").strip() or "auto"
    if setting == "off" and in_container(env):
        return (
            "The link radio's second USB interface is not given to the core container. Plug it in and run "
            "./setup.sh --link-radio SERIAL in the docker folder (./setup.sh --list-interfaces shows the serial "
            "numbers)."
        )
    if setting not in ("off", "auto"):
        return (
            f"The linked radio's interface {setting} is given to the core but could not be opened. Unplug it and plug it "
            "in again, then restart the core."
        )
    return NO_SECOND_INTERFACE


class SerialPortList:
    """The serial ports as the core's own process sees them, for the API to offer (the core reports it as a hardware part).

    The dashboard runs in a container that is given no devices, so a list made there is always empty. Only the core
    can say which ports it was given."""

    def __init__(self, list_ports: Callable[[], list[dict]] = find_serial_ports) -> None:
        self._list = list_ports

    def status(self) -> dict:
        try:
            return {"ports": self._list(), "error": None}
        except Exception as error:  # noqa: BLE001 - the listing must never take the core down
            return {"ports": [], "error": str(error)}


def serial_ports_from_core(remote) -> Callable[[], list[dict]]:
    """The port lister for an API that runs apart from the core: what the core reports. Raises when the core is not
    being heard from, so the page says so instead of showing an empty list that looks like "no adapters"."""

    def list_ports() -> list[dict]:
        status = remote.hardware_status("serial_port_list")  # CoreUnavailable if the core is away
        if status.get("error"):
            raise RuntimeError(status["error"])
        return list(status.get("ports", []))

    return list_ports
