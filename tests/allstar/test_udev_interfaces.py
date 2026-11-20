"""Telling the repeater's interface from the link radio's (#221): the lister and the rule maker behind `setup.sh --link-radio`.

Two interfaces cannot both be /dev/moreopenrepeater-ptt, so the link radio's is named first by serial number (or USB
port) and the repeater's rule skips it. These run docker/udev/interfaces.sh against a made-up /sys."""
import subprocess
from pathlib import Path

DOCKER = Path(__file__).resolve().parents[2] / "docker"
SCRIPT = DOCKER / "udev" / "interfaces.sh"
PTT_RULE = DOCKER / "udev" / "61-moreopenrepeater-ptt.rules"


def add_hidraw(sys_root, name, vendor, product, serial, port):
    usb = sys_root / "devices" / "platform" / "usb1" / port
    usb.mkdir(parents=True, exist_ok=True)
    (usb / "idVendor").write_text(vendor + "\n")
    (usb / "idProduct").write_text(product + "\n")
    if serial is not None:
        (usb / "serial").write_text(serial + "\n")
    hid = usb / f"{port}:1.3" / f"0003:{vendor.upper()}:{product.upper()}.0001"
    hid.mkdir(parents=True)
    node = sys_root / "class" / "hidraw" / name
    node.mkdir(parents=True)
    (node / "device").symlink_to(hid)


def interfaces(*args, sys_root):
    return subprocess.run(["sh", str(SCRIPT), *args], env={"PATH": "/usr/bin:/bin", "MOR_SYS_ROOT": str(sys_root)}, capture_output=True, text=True, timeout=30)


def test_it_lists_both_interfaces_with_the_serial_number_each_answers_to(tmp_path):
    add_hidraw(tmp_path, "hidraw0", "1209", "7388", "A1B2C3", "1-1.2")
    add_hidraw(tmp_path, "hidraw2", "0d8c", "0012", "D4E5F6", "1-1.4")
    out = interfaces("list", sys_root=tmp_path).stdout
    assert "/dev/hidraw0  AIOC  serial A1B2C3  on USB port 1-1.2  ->  --link-radio A1B2C3" in out
    assert "/dev/hidraw2  CM108/CM119 (0d8c:0012)  serial D4E5F6  on USB port 1-1.4  ->  --link-radio D4E5F6" in out


def test_it_says_when_nothing_is_plugged_in(tmp_path):
    assert "No CM108 or AIOC interface is plugged in." in interfaces("list", sys_root=tmp_path).stdout


def test_it_leaves_other_hid_devices_out(tmp_path):
    add_hidraw(tmp_path, "hidraw0", "046d", "c31c", "KEYBOARD", "1-1.1")
    add_hidraw(tmp_path, "hidraw1", "1209", "7388", "A1B2C3", "1-1.2")
    out = interfaces("list", sys_root=tmp_path).stdout
    assert "KEYBOARD" not in out and "A1B2C3" in out


def test_an_interface_with_no_serial_number_is_offered_by_its_usb_port(tmp_path):
    add_hidraw(tmp_path, "hidraw0", "0d8c", "0012", None, "1-1.4")
    out = interfaces("list", sys_root=tmp_path).stdout
    assert "no serial number" in out and "--link-radio port:1-1.4" in out


def test_two_interfaces_with_the_same_serial_number_are_offered_by_their_usb_ports(tmp_path):
    add_hidraw(tmp_path, "hidraw0", "1209", "7388", "00000000", "1-1.2")
    add_hidraw(tmp_path, "hidraw1", "1209", "7388", "00000000", "1-1.4")
    out = interfaces("list", sys_root=tmp_path).stdout
    assert "another interface has the same one" in out
    assert "--link-radio port:1-1.2" in out and "--link-radio port:1-1.4" in out and "--link-radio 00000000" not in out


def test_the_rule_for_a_serial_number_names_that_interface_for_the_link_radio_and_marks_it(tmp_path):
    rule = interfaces("rule", "D4E5F6", sys_root=tmp_path).stdout
    lines = [line for line in rule.splitlines() if line.startswith("SUBSYSTEM")]
    assert len(lines) == 2  # the AIOC and the CM108
    for line in lines:
        assert 'ATTRS{serial}=="D4E5F6"' in line and 'SYMLINK+="moreopenrepeater-link"' in line
        assert 'ENV{MOREOPENREPEATER_LINK}="1"' in line and 'GROUP="audio"' in line and 'MODE="0660"' in line
        assert "moreopenrepeater-ptt" not in line
    assert 'ATTRS{idVendor}=="1209", ATTRS{idProduct}=="7388"' in lines[0] and 'ATTRS{idVendor}=="0d8c"' in lines[1]


def test_the_rule_for_a_usb_port_matches_the_socket_not_a_serial_number(tmp_path):
    rule = interfaces("rule", "port:1-1.4", sys_root=tmp_path).stdout
    assert rule.count('KERNELS=="1-1.4"') == 2 and "ATTRS{serial}" not in rule


def test_a_rule_is_not_made_for_text_that_could_break_out_of_the_rule(tmp_path):
    for bad in ('AB"CD', "a b", "x,SYMLINK+=evil", "port:1-1.4;", "port:", "port:abc"):
        result = interfaces("rule", bad, sys_root=tmp_path)
        assert result.returncode == 2 and result.stdout == "", bad
    assert interfaces("rule", sys_root=tmp_path).returncode == 2
    assert interfaces("nonsense", sys_root=tmp_path).returncode == 2


def test_the_repeaters_rule_skips_an_interface_the_link_rule_marked():
    lines = PTT_RULE.read_text().splitlines()
    guard = lines.index('ENV{MOREOPENREPEATER_LINK}=="1", GOTO="moreopenrepeater_ptt_end"')
    label = lines.index('LABEL="moreopenrepeater_ptt_end"')
    names = [i for i, line in enumerate(lines) if 'SYMLINK+="moreopenrepeater-ptt"' in line]
    assert len(names) == 2 and all(guard < i < label for i in names)  # both name rules are inside what the guard skips


def test_the_repeaters_rule_still_names_both_kinds_of_interface():
    text = PTT_RULE.read_text()
    assert 'ATTRS{idVendor}=="1209", ATTRS{idProduct}=="7388"' in text and 'ATTRS{idVendor}=="0d8c"' in text
    assert "moreopenrepeater-link" not in text.replace("60-moreopenrepeater-link.rules", "").replace("/dev/moreopenrepeater-link", "")
