#!/usr/bin/env python3
"""Bench check for a CM108-family radio interface, before trusting it on air.

Run it on the Pi with the interface plugged in and the repeater stopped
(`sudo systemctl stop moreopenrepeater`), ideally with a dummy load on the
transmitter:

    .venv/bin/python scripts/cm108_check.py [--device /dev/hidraw0] [--outputs 1,4]

It finds the interface, reads its inputs, keys PTT for a second, shows COS
and the GPIO inputs live while you key a radio on the input frequency, and
switches any `--outputs` pins on and off. When it exits, even on Ctrl-C,
PTT is off and the pins it switched are inputs again.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audio_io.cm108 import VOL_DN, VOL_UP, CM108Interface, LinuxHidrawDevice, find_interfaces  # noqa: E402

VENDORS = {0x0D8C: "C-Media", 0x1209: "All In One Cable"}
CHIPS = {
    0x000C: "CM108",
    0x0012: "CM108B",
    0x013C: "CM108AH",
    0x0008: "CM119",
    0x013A: "CM119A",
    0x0013: "CM119B",
    0x6A00: "CM108 (NHRC/N1KDO)",
}
SPARE_PINS = (1, 2, 4, 5, 6, 7, 8)


def chip_name(vendor: int, product: int) -> str:
    if vendor != 0x0D8C:
        return f"{VENDORS.get(vendor, hex(vendor))} {product:04x}"
    return CHIPS.get(product, f"unknown C-Media chip {product:04x}")


def parse_pins(text: str) -> list[int]:
    pins = [int(p) for p in text.split(",") if p.strip()] if text else []
    for pin in pins:
        if pin not in SPARE_PINS:
            raise argparse.ArgumentTypeError(f"GPIO{pin} isn't a spare pin (use {', '.join(map(str, SPARE_PINS))})")
    return pins


def ask(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        print(f"{question} yes")
        return True
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def service_running() -> bool:
    try:
        result = subprocess.run(["systemctl", "is-active", "--quiet", "moreopenrepeater"], check=False)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def describe_inputs(cm108: CM108Interface, buttons: int, gpio: int) -> str:
    cos = "OPEN (carrier)" if cm108.cos_from_buttons(buttons) else "closed"
    vol_up = "low" if buttons & VOL_UP else "high"  # button bits are set while the pin is pulled low
    pins = " ".join(f"{p}:{'H' if gpio & (1 << (p - 1)) else 'L'}" for p in SPARE_PINS)
    return f"COS {cos:14}  VOL_UP {vol_up:4}  GPIO {pins}  raw {buttons:02x} {gpio:02x}"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--device", help="hidraw node (default: the only CM108 found)")
    parser.add_argument("--ptt-seconds", type=float, default=1.0, help="how long to key PTT (up to 5, 0 skips it)")
    parser.add_argument("--watch-seconds", type=float, default=15.0, help="how long to watch COS and the inputs")
    parser.add_argument("--outputs", type=parse_pins, default=[], help="spare pins to switch on and off, e.g. 1,4")
    parser.add_argument("--yes", action="store_true", help="don't ask before keying or switching")
    args = parser.parse_args(argv)
    if not 0 <= args.ptt_seconds <= 5:
        parser.error("--ptt-seconds is 0 to 5")

    interfaces = find_interfaces()
    for found in interfaces:
        print(f"Found {chip_name(found['vendor'], found['product'])} at {found['path']} ({found['name']})")
    path = args.device
    if path is None:
        if len(interfaces) != 1:
            print("No CM108 interface found." if not interfaces else "More than one found: choose one with --device.")
            return 1
        path = interfaces[0]["path"]
    if service_running():
        print("The moreopenrepeater service is running and would fight over PTT. Stop it first:")
        print("  sudo systemctl stop moreopenrepeater")
        return 1

    try:
        device = LinuxHidrawDevice(path)
    except PermissionError:
        print(f"No permission to open {path}. Install packaging/99-cm108.rules and add this user to the audio group")
        print("(docs/raspberry-pi.md, section 3), then log in again.")
        return 1
    cm108 = CM108Interface(device)
    try:
        try:
            buttons, gpio = cm108.read_inputs()
        except OSError as error:
            print(f"Reading the inputs failed: {error}. This needs Linux 5.11 or later (uname -r).")
            return 1
        print(f"Inputs read OK: {describe_inputs(cm108, buttons, gpio)}")

        if args.ptt_seconds and ask(f"\nKey the transmitter (GPIO3) for {args.ptt_seconds:g} s?", args.yes):
            cm108.set_ptt(True)
            print("PTT on", flush=True)
            time.sleep(args.ptt_seconds)
            cm108.set_ptt(False)
            print("PTT off. Did the transmitter key and unkey?")

        if args.watch_seconds:
            print(f"\nWatching the inputs for {args.watch_seconds:g} s. Key a radio on the receive frequency:")
            print("COS should open while you transmit and close when you stop.")
            last = None
            opened = False
            end = time.monotonic() + args.watch_seconds
            while time.monotonic() < end:
                buttons, gpio = cm108.read_inputs()
                line = describe_inputs(cm108, buttons, gpio)
                opened = opened or cm108.cos_from_buttons(buttons)
                if line != last:
                    print(f"  {time.strftime('%H:%M:%S')}  {line}", flush=True)
                    last = line
                time.sleep(0.05)
            if not opened:
                print("COS never opened. Check the COS wire, and that it pulls the interface's COS input low.")
                print(f"(COS is read from bit {VOL_DN:#04x} of the first raw byte.)")

        for pin in args.outputs:
            if ask(f"\nSwitch GPIO{pin} on for 2 s?", args.yes):
                cm108.set_gpio(pin, True)
                print(f"GPIO{pin} on", flush=True)
                time.sleep(2)
                cm108.set_gpio(pin, False)
                print(f"GPIO{pin} off")
    except KeyboardInterrupt:
        print()
    finally:
        cm108.set_ptt(False)
        for pin in SPARE_PINS:
            cm108.release_gpio(pin)
        device.close()
        print("PTT is off.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
