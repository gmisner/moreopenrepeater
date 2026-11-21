"""Support bundles: a zip of recent logs and safe diagnostics for
troubleshooting radio configuration, audio and related setup. No passwords,
env file, users, tokens or recordings.
"""
from __future__ import annotations

import collections
import json
import re
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

from audio_io.cm108 import find_interfaces
from contracts import CoreHostFacts

from .audio_devices import alsa_card_stable_id
from .boards import alsa_card
from .log_history import LogHistory, LogHistoryRefused, LogHistoryUnavailable
from .mixer import parse_volumes
from .service.core_handle import ApiCoreHandle, CoreUnavailable
from .updates import Updater, installed_version, read_log

LOG_LINES = 5000
JOURNAL_LINES = 500
UPDATE_LOG_LINES = 500
KERNEL_LINES = 400
HISTORY_EVENTS = 500  # warnings and worse from the collector, newest first
_USB_KERNEL = re.compile(r"usb|hidraw|hid-generic|cm108|aioc|disconnect|reset full-speed|cdc_acm", re.I)
_HID_UDEV_KEYS = (
    "DEVNAME",
    "ID_VENDOR",
    "ID_VENDOR_FROM_DATABASE",
    "ID_MODEL",
    "ID_MODEL_FROM_DATABASE",
    "ID_SERIAL",
    "ID_USB_DRIVER",
    "ID_USB_INTERFACE_NUM",
)


def _event_history(log_history: Optional[LogHistory]) -> dict:
    """The collector's warnings and worse, and the gaps it knows of, or why there is none. The events were
    redacted when they were made; nothing here reads more than the collector keeps."""
    if log_history is None or not log_history.configured:
        return {"available": False, "reason": "the log collector is not set up on this install"}
    try:
        return {
            "available": True,
            "stats": log_history.stats(),
            "gaps": log_history.gaps(limit=100)["gaps"],
            "events": log_history.events(min_severity="warning", limit=HISTORY_EVENTS)["events"],
        }
    except (LogHistoryUnavailable, LogHistoryRefused) as error:
        return {"available": False, "reason": str(error)}


def support_bundle_name(callsign: str, now: float) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", callsign.lower()).strip("-") or "repeater"
    return f"moreopenrepeater-support-{slug}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}.zip"


def _tail_lines(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    with path.open(errors="replace") as handle:
        return [line.rstrip("\n") for line in collections.deque(handle, maxlen=lines)]


def _run_text(run: Callable[..., subprocess.CompletedProcess], argv: list[str], *, timeout: float = 15) -> str:
    try:
        result = run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        return f"({' '.join(argv)} unavailable: {error})\n"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        return f"({' '.join(argv)} failed: {err})\n"
    return result.stdout if result.stdout else "(empty)\n"


def _journal(run: Callable[..., subprocess.CompletedProcess], lines: int) -> str:
    text = _run_text(run, ["journalctl", "-u", "moreopenrepeater", "-n", str(lines), "--no-pager"])
    if _unavailable(text):
        return text.rstrip("\n") + "\n(there is no systemd journal for the service here: its log is in moreopenrepeater.log, and in core.log for the core)\n"
    return text


def runner_with_mixer(run_mixer: Callable[[list[str]], subprocess.CompletedProcess]) -> Callable[..., subprocess.CompletedProcess]:
    """The bundle's `run`, with `amixer` going through the dashboard's own mixer runner (in the container install that asks the
    core, which has the sound card) and everything else run here."""

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        if argv and argv[0] == "amixer":
            return run_mixer(argv)
        return subprocess.run(argv, **kwargs)

    return run


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError as error:
        return f"({path} unavailable: {error})\n"


def _unavailable(text: str) -> bool:
    """`_run_text` and `_read_text` say what they could not do in parentheses."""
    return text.startswith("(")


def _sys_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(errors="replace").strip() or None
    except OSError:
        return None


def _sysfs_usb_devices(root: Path = Path("/")) -> str:
    """The USB devices as the kernel lists them in /sys, for where there is no `lsusb` (a container image has none; `/sys` is
    there). One line per device: its place on the bus, vendor:product, what it calls itself, and its speed."""
    lines = []
    for device in sorted((root / "sys/bus/usb/devices").glob("*")):
        vendor, product = _sys_text(device / "idVendor"), _sys_text(device / "idProduct")
        if not vendor or not product:  # an interface of a device, not a device
            continue
        name = " ".join(filter(None, (_sys_text(device / "manufacturer"), _sys_text(device / "product"))))
        speed = _sys_text(device / "speed")
        lines.append(f"{device.name}: {vendor}:{product} {name}".rstrip() + (f" ({speed} Mbit/s)" if speed else ""))
    return "\n".join(lines) if lines else "(no USB devices in /sys/bus/usb/devices)"


def _sysfs_sound_cards(root: Path = Path("/")) -> str:
    """The sound cards from /sys/class/sound, for where /proc/asound is hidden (Docker masks it)."""
    lines = []
    for card in sorted((root / "sys/class/sound").glob("card[0-9]*"), key=lambda path: int(path.name[4:] or 0)):
        number = int(card.name[4:])
        lines.append(f"card {number}: id={_sys_text(card / 'id') or '?'} usb={alsa_card_stable_id(number, sys_root=root) or 'not usb'}")
    return "\n".join(lines) if lines else "(no sound cards in /sys/class/sound)"


def _sysfs_hidraw(sysfs: Path = Path("/sys/class/hidraw")) -> str:
    entries = find_interfaces(sysfs)
    if not entries:
        return "(no CM108-family hidraw devices in /sys/class/hidraw)"
    return "\n".join(f"{entry['path']}: {entry['vendor']:04x}:{entry['product']:04x} {entry['name']} ({entry['stable_id']})" for entry in entries)


def _hidraw_devices(
    run: Callable[..., subprocess.CompletedProcess], dev: Path = Path("/dev"), sysfs: Path = Path("/sys/class/hidraw")
) -> str:
    paths = sorted(dev.glob("hidraw*"))
    if not paths:
        return "(no /dev/hidraw* devices here; the ones /sys lists:)\n" + _sysfs_hidraw(sysfs) + "\n"
    chunks: list[str] = []
    for path in paths:
        chunks.append(f"## {path}")
        try:
            chunks.append(f"mode={oct(path.stat().st_mode & 0o777)} owner={path.owner()} group={path.group()}")
        except OSError as error:
            chunks.append(f"(stat failed: {error})")
        info = _run_text(run, ["udevadm", "info", "-q", "property", "-n", str(path)], timeout=5)
        for line in info.splitlines():
            key = line.split("=", 1)[0]
            if key in _HID_UDEV_KEYS:
                chunks.append(line)
        chunks.append("")
    return "\n".join(chunks) + ("\n" if not chunks[-1:] == [""] else "")


_NO_KERNEL_LOG = "(the host's kernel log is not visible from here; on the host: dmesg -T | grep -i usb)"


def _kernel_from_core(core_host_facts: Callable[[], CoreHostFacts]) -> tuple[Optional[list[str]], str]:
    """The kernel log lines the core could read (the container install), or None and why not. Never raises: the bundle is for
    when things are broken."""
    try:
        facts = core_host_facts()
    except Exception as error:  # noqa: BLE001 - whatever went wrong is the reason
        return None, f"could not ask the core for it: {error}"
    if facts.kernel_log is None:
        return None, f"asked the core, which has none: {facts.kernel_error}"
    return facts.kernel_log, ""


def _usb_lines(lines: list[str]) -> str:
    matched = [line for line in lines if _USB_KERNEL.search(line)]
    return "\n".join(matched) if matched else "(no usb/hid lines in recent kernel log)"


def _usb_snapshot(
    run: Callable[..., subprocess.CompletedProcess],
    root: Path = Path("/"),
    core_host_facts: Optional[Callable[[], CoreHostFacts]] = None,
) -> str:
    """lsusb, ALSA cards, hidraw udev props, and recent USB-related kernel log. Where a command or a /proc file is not there
    (a container), the same facts come from /sys, and the section says so. `core_host_facts` asks the core for the kernel
    log, which in the container install is the only one that can have it (it was given the host's /dev/kmsg, read-only)."""
    lsusb = _run_text(run, ["lsusb"]).rstrip("\n")
    if _unavailable(lsusb):
        lsusb = "(lsusb is not available here; from /sys/bus/usb/devices:)\n" + _sysfs_usb_devices(root)
    cards = _read_text(root / "proc/asound/cards").rstrip("\n")
    pcm = _read_text(root / "proc/asound/pcm").rstrip("\n")
    if _unavailable(cards):
        cards = "(/proc/asound is not visible here; from /sys/class/sound:)\n" + _sysfs_sound_cards(root)
        pcm = "(not visible here)"
    parts = [
        "=== lsusb ===",
        lsusb,
        "",
        "=== ALSA cards ===",
        cards,
        "",
        "=== /proc/asound/pcm ===",
        pcm,
        "",
        "=== hidraw ===",
        _hidraw_devices(run).rstrip("\n"),
        "",
        "=== kernel (usb/hid, recent) ===",
    ]
    from_core, why_not = _kernel_from_core(core_host_facts) if core_host_facts is not None else (None, "")
    if from_core is not None:
        parts.append("(from the core, which reads the host's /dev/kmsg)")
        parts.append(_usb_lines(from_core))
    else:
        kernel = _run_text(run, ["journalctl", "-k", "-n", str(KERNEL_LINES), "--no-pager"])
        if _unavailable(kernel):
            parts.append(kernel.rstrip("\n") + "\n" + _NO_KERNEL_LOG + (f"\n({why_not})" if why_not else ""))
        else:
            parts.append(_usb_lines(kernel.splitlines()))
    parts.append("")
    return "\n".join(parts) + "\n"


def _mixer_for_device(device_name: str, run: Callable[..., subprocess.CompletedProcess]) -> dict[str, Any]:
    """ALSA card number and amixer volumes for a PortAudio device name."""
    card = alsa_card(device_name)
    if card is None:
        return {"device": device_name, "card": None, "error": "no ALSA hw:N in device name"}
    try:
        result = run(["amixer", "-c", str(card), "scontents"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as error:
        return {"device": device_name, "card": card, "error": str(error)}
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        return {"device": device_name, "card": card, "error": err}
    raw = result.stdout or ""
    return {
        "device": device_name,
        "card": card,
        "volumes": [
            {"control": v.control, "direction": v.direction, "min": v.min, "max": v.max, "value": v.value, "db": v.db}
            for v in parse_volumes(raw)
        ],
        "amixer_scontents": raw,
    }


CORE_NOT_REACHABLE = "unavailable (core not reachable)"


def bundle_callsign(service: ApiCoreHandle) -> str:
    """The callsign for the bundle's name; a repeater whose core cannot be reached still gets a bundle."""
    try:
        return service.config.callsign or "repeater"
    except CoreUnavailable:
        return "repeater"


def _settings_for_bundle(service: ApiCoreHandle) -> dict:
    """The core's settings, or a short note saying they could not be read. Never an old copy, and never anything
    beyond what `export_snapshot` already gives (the bundle exists to be sent to a helper, so the note carries no detail)."""
    try:
        return service.export_snapshot()
    except CoreUnavailable:
        return {"settings": CORE_NOT_REACHABLE}


def _devices_snapshot(
    service: ApiCoreHandle,
    *,
    audio_devices: Optional[list],
    serial_ports: Optional[list],
    cm108_interfaces: Optional[list],
    cm108_path: Optional[str],
    run: Callable[..., subprocess.CompletedProcess],
) -> dict[str, Any]:
    """PortAudio devices, serial ports, CM108 hidraw, and mixer on selected cards."""
    try:
        if getattr(service, "stale", False):  # an old copy of the selections is not what is configured now
            raise CoreUnavailable("the core is not being heard from")
        config = service.config
    except CoreUnavailable:
        return {
            "selected": CORE_NOT_REACHABLE,
            "audio_devices": list(audio_devices or []),
            "serial_ports": list(serial_ports or []),
            "cm108_interfaces": list(cm108_interfaces or []),
            "cm108_path": cm108_path,
            "mixer": {},
        }
    selected = {
        "audio_input_device": config.audio_input_device,
        "audio_output_device": config.audio_output_device,
        "cos_source": config.cos_source,
        "ptt_output": config.ptt_output,
        "cos_serial_device": config.cos_serial_device,
        "ptt_serial_device": config.ptt_serial_device,
        "board_preset": config.board_preset,
    }
    mixer_by_card: dict[str, Any] = {}
    for name in (config.audio_input_device, config.audio_output_device):
        if not name:
            continue
        snap = _mixer_for_device(name, run)
        card = snap.get("card")
        key = str(card) if card is not None else name
        if key in mixer_by_card:
            mixer_by_card[key].setdefault("also_used_as", []).append(name)
        else:
            mixer_by_card[key] = snap
    return {
        "selected": selected,
        "audio_devices": list(audio_devices or []),
        "serial_ports": list(serial_ports or []),
        "cm108_interfaces": list(cm108_interfaces or []),
        "cm108_path": cm108_path,
        "mixer": mixer_by_card,
    }


def write_support_bundle(
    path: Path,
    *,
    service: ApiCoreHandle,
    log_path: Path,
    repo_dir: Path,
    update_channel: str,
    live_audio_status: Optional[dict],
    system_readings: Optional[dict],
    updater: Optional[Updater],
    now: float,
    receiver_openings: Optional[list] = None,
    audio_glitches: Optional[list] = None,
    last_unexpected_stop: Optional[dict] = None,
    audio_devices: Optional[list] = None,
    serial_ports: Optional[list] = None,
    cm108_interfaces: Optional[list] = None,
    cm108_path: Optional[str] = None,
    log_history: Optional[LogHistory] = None,
    core_log_lines: Optional[list[str]] = None,
    core_host_facts: Optional[Callable[[], CoreHostFacts]] = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    """Writes a support-bundle archive to `path`. `core_log_lines`: the core's recent log lines, when the core is a separate
    process (its log is not in this process's file). `core_host_facts`: asks that core for what it sees of the host (its
    kernel log); None where this process is the one with the host."""
    callsign = bundle_callsign(service)
    version = installed_version(repo_dir)
    settings = _settings_for_bundle(service)
    try:
        status = vars(service.snapshot())
    except CoreUnavailable:
        status = {"state": CORE_NOT_REACHABLE}
    update_lines = read_log(updater.settings, UPDATE_LOG_LINES) if updater and updater.settings else []
    health = {
        "system": system_readings if system_readings is not None else {},
        "last_unexpected_stop": last_unexpected_stop,
    }
    devices = _devices_snapshot(
        service,
        audio_devices=audio_devices,
        serial_ports=serial_ports,
        cm108_interfaces=cm108_interfaces,
        cm108_path=cm108_path,
        run=run,
    )
    readme = (
        "moreopenrepeater support bundle\n"
        "================================\n"
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S %z', time.localtime(now))}\n"
        f"Callsign:  {callsign}\n"
        f"Channel:   {update_channel}\n"
        "\n"
        "Contents for radio/audio troubleshooting:\n"
        "  config.json         settings, macros, announcements (a note instead when the core could not be reached)\n"
        "  status.json         controller state at capture\n"
        "  audio-engine.json   levels, COS, TX, device names\n"
        "  devices.json        PortAudio list, serial ports, CM108, amixer\n"
        "  openings.json       recent squelch openings (levels, DTMF, after_tx)\n"
        "  glitches.json       audio underruns/overruns\n"
        "  health.json         Pi readings + last unexpected stop\n"
        "  usb.txt             lsusb, ALSA cards, hidraw, USB kernel lines\n"
        "  moreopenrepeater.log / journal.txt / update.log\n"
        "  core.log            the core's own lines, when it runs apart from the dashboard (the container install)\n"
        "  event-history.json  warnings and worse from the log collector, with what it knows is missing\n"
        "\n"
        "It does not include passwords, the env file, users, tokens or recordings.\n"
        "Share the whole .zip with whoever is helping you.\n"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", readme)
        archive.writestr(
            "version.json",
            json.dumps({"channel": update_channel, "installed": version}, indent=2) + "\n",
        )
        archive.writestr("config.json", json.dumps(settings, indent=2, default=str) + "\n")
        archive.writestr("status.json", json.dumps(status, indent=2, default=str) + "\n")
        archive.writestr(
            "audio-engine.json",
            json.dumps(live_audio_status if live_audio_status is not None else {}, indent=2, default=str) + "\n",
        )
        archive.writestr("devices.json", json.dumps(devices, indent=2, default=str) + "\n")
        archive.writestr(
            "openings.json",
            json.dumps(list(receiver_openings or []), indent=2, default=str) + "\n",
        )
        archive.writestr(
            "glitches.json",
            json.dumps(list(audio_glitches or []), indent=2, default=str) + "\n",
        )
        archive.writestr("health.json", json.dumps(health, indent=2, default=str) + "\n")
        archive.writestr(
            "system.json",
            json.dumps(system_readings if system_readings is not None else {}, indent=2, default=str) + "\n",
        )
        archive.writestr("usb.txt", _usb_snapshot(run, core_host_facts=core_host_facts))
        archive.writestr("moreopenrepeater.log", "\n".join(_tail_lines(log_path, LOG_LINES)) + "\n")
        if core_log_lines is not None:
            archive.writestr("core.log", "\n".join(core_log_lines[-LOG_LINES:]) + "\n")
        archive.writestr("journal.txt", _journal(run, JOURNAL_LINES))
        archive.writestr("event-history.json", json.dumps(_event_history(log_history), indent=2, default=str) + "\n")
        archive.writestr("update.log", "\n".join(update_lines) + ("\n" if update_lines else ""))
