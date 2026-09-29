import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "cm108_check.py"
spec = importlib.util.spec_from_file_location("cm108_check", SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class FakeDevice:
    def __init__(self, path):
        self.writes = []
        self.reads = 0
        self.closed = False

    def write_report(self, report):
        self.writes.append((report[2], report[3]))

    def get_input_report(self, length):
        self.reads += 1
        return bytes([0x00 if self.reads % 20 < 10 else 0x02, 0x01, 0, 0])  # COS opens and closes

    def close(self):
        self.closed = True


def hidraw(root, name, hid_id, hid_name):
    (root / name / "device").mkdir(parents=True)
    (root / name / "device" / "uevent").write_text(f"DRIVER=hid-generic\nHID_ID={hid_id}\nHID_NAME={hid_name}\n")


def test_finds_c_media_interfaces(tmp_path):
    hidraw(tmp_path, "hidraw0", "0003:0000046D:0000C52B", "Logitech receiver")
    hidraw(tmp_path, "hidraw1", "0003:00000D8C:0000013C", "C-Media Electronics Inc. USB Audio Device")
    hidraw(tmp_path, "hidraw2", "0003:00001209:00007388", "All In One Cable")
    found = check.find_interfaces(tmp_path)
    assert [f["path"] for f in found] == ["/dev/hidraw1", "/dev/hidraw2"]
    assert check.chip_name(found[0]["vendor"], found[0]["product"]) == "CM108AH"


def test_runs_the_checks_and_leaves_ptt_off(monkeypatch, capsys):
    devices = []
    monkeypatch.setattr(check, "find_interfaces", lambda: [{"path": "/dev/hidraw9", "vendor": 0x0D8C, "product": 0x000C, "name": "x"}])
    monkeypatch.setattr(check, "service_running", lambda: False)
    monkeypatch.setattr(check, "LinuxHidrawDevice", lambda path: devices.append(FakeDevice(path)) or devices[-1])
    monkeypatch.setattr(check.time, "sleep", lambda s: None)
    assert check.main(["--yes", "--watch-seconds", "0.05", "--outputs", "1"]) == 0
    out = capsys.readouterr().out
    assert "Found CM108 at /dev/hidraw9" in out and "PTT on" in out and "GPIO1 on" in out
    assert "COS OPEN" in out
    device = devices[0]
    assert device.writes[:2] == [(0x04, 0x04), (0x00, 0x04)]  # PTT on, off
    assert device.writes[-1] == (0x00, 0x04)  # PTT still driven low, GPIO1 an input again
    assert device.closed


def test_refuses_while_the_service_runs(monkeypatch, capsys):
    monkeypatch.setattr(check, "find_interfaces", lambda: [{"path": "/dev/hidraw9", "vendor": 0x0D8C, "product": 0x000C, "name": "x"}])
    monkeypatch.setattr(check, "service_running", lambda: True)
    assert check.main([]) == 1
    assert "systemctl stop moreopenrepeater" in capsys.readouterr().out
