"""The support bundle where `lsusb`, `journalctl`, `/proc/asound` and `/dev/hidraw*` are not there (the container install)."""
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace


from api.log_hub import LogHub
from api.routes.admin_ops import _audio_devices_for_bundle, _core_log_lines
from api.support_bundle import (
    _hidraw_devices,
    _journal,
    _sysfs_hidraw,
    _sysfs_sound_cards,
    _sysfs_usb_devices,
    _usb_snapshot,
    runner_with_mixer,
    write_support_bundle,
)


def put(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def usb_device(root: Path, name: str, vendor: str, product: str, manufacturer=None, title=None, speed="12") -> None:
    base = f"sys/bus/usb/devices/{name}"
    put(root, f"{base}/idVendor", vendor + "\n")
    put(root, f"{base}/idProduct", product + "\n")
    if manufacturer:
        put(root, f"{base}/manufacturer", manufacturer + "\n")
    if title:
        put(root, f"{base}/product", title + "\n")
    put(root, f"{base}/speed", speed + "\n")


def missing(argv, **kwargs):
    raise FileNotFoundError(argv[0])


def test_usb_devices_come_from_sysfs_one_line_each_without_the_interfaces(tmp_path):
    usb_device(tmp_path, "usb1", "1d6b", "0002", "Linux", "DWC OTG Controller", speed="480")
    usb_device(tmp_path, "1-1.2", "1209", "7388", "AIOC", "All-In-One-Cable")
    put(tmp_path, "sys/bus/usb/devices/1-1.2:1.0/bInterfaceClass", "01\n")  # an interface: no idVendor, not a device
    lines = _sysfs_usb_devices(tmp_path).splitlines()
    assert lines == [
        "1-1.2: 1209:7388 AIOC All-In-One-Cable (12 Mbit/s)",
        "usb1: 1d6b:0002 Linux DWC OTG Controller (480 Mbit/s)",
    ]


def test_a_device_with_no_name_or_speed_is_still_listed(tmp_path):
    put(tmp_path, "sys/bus/usb/devices/1-1/idVendor", "0424\n")
    put(tmp_path, "sys/bus/usb/devices/1-1/idProduct", "2514\n")
    assert _sysfs_usb_devices(tmp_path) == "1-1: 0424:2514"


def test_no_usb_devices_says_so(tmp_path):
    assert "no USB devices" in _sysfs_usb_devices(tmp_path)


def test_sound_cards_come_from_sysfs_in_card_order_with_their_usb_identity(tmp_path):
    for number, ident in ((10, "Ten"), (2, "Cable"), (0, "Headphones")):
        put(tmp_path, f"sys/class/sound/card{number}/id", ident + "\n")
    usb = tmp_path / "sys/devices/usb/1-1.2"
    usb.mkdir(parents=True)
    (usb / "idVendor").write_text("1209\n")
    (usb / "idProduct").write_text("7388\n")
    (usb / "serial").write_text("AIOC42\n")
    (tmp_path / "sys/class/sound/card2/device").symlink_to(usb)
    lines = _sysfs_sound_cards(tmp_path).splitlines()
    assert [line.split(":")[0] for line in lines] == ["card 0", "card 2", "card 10"]
    assert "id=Cable usb=usb-1209:7388-AIOC42" in lines[1] and "usb=not usb" in lines[0]


def test_hidraw_devices_come_from_sysfs_when_there_are_no_nodes(tmp_path):
    node = tmp_path / "hidraw0"
    put(node, "device/uevent", "HID_ID=0003:00001209:00007388\nHID_NAME=AIOC All-In-One-Cable\n")
    text = _sysfs_hidraw(tmp_path)
    assert "/dev/hidraw0: 1209:7388 AIOC All-In-One-Cable" in text
    assert "no CM108-family" in _sysfs_hidraw(tmp_path / "nothing")


def test_with_no_hidraw_nodes_the_snapshot_lists_what_sysfs_knows_and_with_nodes_it_describes_them(tmp_path):
    sysfs = tmp_path / "sys"
    put(sysfs / "hidraw0", "device/uevent", "HID_ID=0003:00001209:00007388\nHID_NAME=AIOC All-In-One-Cable\n")
    dev = tmp_path / "dev"
    dev.mkdir()
    bare = _hidraw_devices(missing, dev, sysfs)
    assert "no /dev/hidraw* devices here" in bare and "/dev/hidraw0: 1209:7388 AIOC" in bare
    (dev / "hidraw0").write_text("")
    described = _hidraw_devices(
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "ID_MODEL=All-In-One-Cable\nOTHER=x\n", ""), dev, sysfs
    )
    assert "## " in described and "hidraw0" in described and "ID_MODEL=All-In-One-Cable" in described
    assert "OTHER=x" not in described and "no /dev/hidraw* devices here" not in described


def test_where_the_commands_and_proc_files_are_missing_the_snapshot_uses_sysfs_and_says_so(tmp_path):
    usb_device(tmp_path, "1-1.2", "1209", "7388", "AIOC", "All-In-One-Cable")
    put(tmp_path, "sys/class/sound/card2/id", "Cable\n")
    text = _usb_snapshot(missing, tmp_path)
    assert "(lsusb is not available here; from /sys/bus/usb/devices:)" in text and "1-1.2: 1209:7388 AIOC" in text
    assert "(/proc/asound is not visible here; from /sys/class/sound:)" in text and "card 2: id=Cable" in text
    assert "kernel log is not visible from here" in text and "dmesg -T" in text
    assert "(no usb/hid lines" not in text  # not claiming there was a log with nothing in it


def test_where_the_commands_and_proc_files_exist_the_snapshot_is_what_it_always_was(tmp_path):
    put(tmp_path, "proc/asound/cards", " 2 [Cable ]: USB-Audio - All-In-One-Cable\n")
    put(tmp_path, "proc/asound/pcm", "02-00: USB Audio : USB Audio : playback 1 : capture 1\n")

    def run(argv, **kwargs):
        if argv[0] == "lsusb":
            return subprocess.CompletedProcess(argv, 0, "Bus 001 Device 004: ID 1209:7388 AIOC\n", "")
        if argv[:2] == ["journalctl", "-k"]:
            return subprocess.CompletedProcess(argv, 0, "usb 1-1.2: USB disconnect\nunrelated line\n", "")
        raise FileNotFoundError(argv[0])

    text = _usb_snapshot(run, tmp_path)
    assert "Bus 001 Device 004: ID 1209:7388 AIOC" in text and "from /sys" not in text.split("=== hidraw ===")[0]
    assert "USB-Audio - All-In-One-Cable" in text and "02-00: USB Audio" in text
    assert "usb 1-1.2: USB disconnect" in text and "unrelated line" not in text


def test_without_a_journal_the_bundle_says_where_the_log_is_instead():
    text = _journal(missing, 500)
    assert "unavailable" in text and "no systemd journal for the service here" in text and "moreopenrepeater.log" in text and "core.log" in text


def test_amixer_goes_through_the_dashboards_mixer_and_everything_else_runs_here(monkeypatch):
    asked = []
    run = runner_with_mixer(lambda argv: asked.append(argv) or subprocess.CompletedProcess(argv, 0, "from the core", ""))
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "from here", ""))
    assert run(["amixer", "-c", "2", "scontents"], capture_output=True, text=True, timeout=15).stdout == "from the core"
    assert run(["lsusb"], capture_output=True, text=True, timeout=15).stdout == "from here"
    assert asked == [["amixer", "-c", "2", "scontents"]]


def test_the_core_s_lines_are_in_the_bundle_only_when_the_core_is_another_process():
    hub = LogHub()
    hub.publish("core line one")
    hub.publish("core line two")
    assert _core_log_lines(SimpleNamespace(is_remote=True), hub) == ["core line one", "core line two"]
    assert _core_log_lines(SimpleNamespace(is_remote=False), hub) is None
    assert _core_log_lines(SimpleNamespace(), hub) is None
    assert _core_log_lines(SimpleNamespace(is_remote=True), None) is None


def test_a_sound_card_list_that_fails_is_a_line_in_the_bundle_not_a_failed_download():
    def broken():
        raise RuntimeError("the core is not being heard from")

    assert _audio_devices_for_bundle(broken) == [{"error": "couldn't list audio devices: the core is not being heard from"}]
    assert _audio_devices_for_bundle(lambda: [{"name": "x"}]) == [{"name": "x"}]


def write(tmp_path, **kwargs):
    from api.service import RepeaterService

    path = tmp_path / "bundle.zip"
    log = tmp_path / "api.log"
    log.write_text("api line\n")
    write_support_bundle(
        path, service=RepeaterService(), log_path=log, repo_dir=tmp_path, update_channel="stable", live_audio_status={},
        system_readings={}, updater=None, now=0.0, run=missing, **kwargs,
    )
    return zipfile.ZipFile(path)


def test_the_bundle_carries_core_log_only_when_given_the_cores_lines(tmp_path):
    with write(tmp_path, core_log_lines=["core a", "core b"]) as archive:
        assert archive.read("core.log").decode() == "core a\ncore b\n"
    with write(tmp_path) as archive:
        assert "core.log" not in archive.namelist()
