import errno

import pytest

from audio_io.cm108 import GPIO1, GPIO3, GPIO4, VOL_DN, VOL_UP, CM108Interface, _hidiocginput, gpio_bit


class FakeHidrawDevice:
    """In-memory stand-in for /dev/hidrawN so CM108Interface's bit-packing
    logic can be tested without real hardware."""

    def __init__(self, buttons: int = 0, gpio: int = 0, write_error: OSError | None = None) -> None:
        self.written_reports: list[bytes] = []
        self.buttons = buttons
        self.gpio = gpio
        self.write_error = write_error
        self.path = "/dev/hidraw9"

    def write_report(self, report: bytes) -> None:
        if self.write_error is not None:
            raise self.write_error
        self.written_reports.append(report)

    def get_input_report(self, length: int) -> bytes:
        return bytes([self.buttons, self.gpio, 0, 0])[:length]

    def close(self) -> None:
        pass


def test_set_ptt_active_writes_data_before_mask_with_pin_bit_set():
    device = FakeHidrawDevice()
    CM108Interface(device).set_ptt(active=True)
    assert device.written_reports == [bytes([0x00, 0x00, GPIO3, GPIO3, 0x00])]


def test_set_ptt_inactive_writes_zero_data_with_pin_still_marked_as_output():
    device = FakeHidrawDevice()
    CM108Interface(device).set_ptt(active=False)
    assert device.written_reports == [bytes([0x00, 0x00, 0x00, GPIO3, 0x00])]


def test_every_write_keeps_the_other_outputs():
    device = FakeHidrawDevice()
    cm108 = CM108Interface(device)
    cm108.set_gpio(1, True)
    cm108.set_ptt(True)
    cm108.set_ptt(False)
    assert device.written_reports == [
        bytes([0x00, 0x00, GPIO1, GPIO1, 0x00]),
        bytes([0x00, 0x00, GPIO1 | GPIO3, GPIO1 | GPIO3, 0x00]),
        bytes([0x00, 0x00, GPIO1, GPIO1 | GPIO3, 0x00]),
    ]
    assert cm108.output_level(1) is True and cm108.output_level(3) is False and cm108.output_level(4) is None


def test_releasing_a_pin_makes_it_an_input_again():
    device = FakeHidrawDevice()
    cm108 = CM108Interface(device)
    cm108.set_gpio(4, True)
    cm108.set_ptt(True)
    cm108.release_gpio(4)
    cm108.release_gpio(2)  # never an output: nothing to write
    assert device.written_reports[-1] == bytes([0x00, 0x00, GPIO3, GPIO3, 0x00])
    assert len(device.written_reports) == 3


def test_cos_is_vol_dn_active_low():
    # A button bit is set while its pin is pulled low, like chan_simpleusb's
    # carrierfrom=usbinvert ("active low"), which reports carrier on a set bit.
    device = FakeHidrawDevice(buttons=VOL_DN)
    cm108 = CM108Interface(device)
    assert cm108.read_cos() is True
    device.buttons = 0xFF & ~VOL_DN
    assert cm108.read_cos() is False


def test_cos_input_and_polarity_can_change():
    device = FakeHidrawDevice(buttons=VOL_DN)
    cm108 = CM108Interface(device, cos_input=VOL_UP, cos_active_low=False)
    assert cm108.read_cos() is True  # VOL_UP's pin is high: active high carrier
    device.buttons = VOL_UP
    assert cm108.read_cos() is False


def test_gpio_inputs_come_from_the_second_byte():
    device = FakeHidrawDevice(buttons=GPIO1, gpio=GPIO4)
    cm108 = CM108Interface(device)
    assert cm108.read_gpio(4) is True
    assert cm108.read_gpio(1) is False
    assert cm108.last_inputs == (GPIO1, GPIO4)


def test_gpio_pins_1_to_8():
    assert [gpio_bit(pin) for pin in (1, 3, 8)] == [0x01, 0x04, 0x80]
    with pytest.raises(ValueError):
        gpio_bit(9)


def test_hidiocginput_matches_the_kernel_header():
    assert _hidiocginput(5) == 0xC005480A


def test_writes_are_ignored_when_the_hidraw_device_stays_gone():
    """Unplug with no replacement: unkey must not raise, and local state still tracks."""
    device = FakeHidrawDevice(write_error=OSError(errno.ENODEV, "No such device"))

    def still_gone(path: str) -> FakeHidrawDevice:
        raise OSError(errno.ENOENT, "No such file or directory")

    cm108 = CM108Interface(device, find=lambda: [], open_device=still_gone)
    cm108.set_ptt(True)
    cm108.set_ptt(False)
    cm108.set_gpio(1, True)
    assert device.written_reports == []
    assert cm108._gone is True
    assert cm108.output_level(3) is False
    assert cm108.output_level(1) is True


def test_next_ptt_reopens_hidraw_and_rewrites_gpio_state():
    """After a simulated disconnect, the next PTT write finds the interface again."""
    original = FakeHidrawDevice()
    original.path = "/dev/hidraw3"
    replacement = FakeHidrawDevice()
    replacement.path = "/dev/hidraw4"

    cm108 = CM108Interface(
        original,
        find=lambda: [{"path": "/dev/hidraw4", "vendor": 0x0D8C, "product": 0x013C, "name": "AIOC"}],
        open_device=lambda path: replacement,
    )
    cm108.set_gpio(1, True)
    cm108.set_ptt(True)
    # Simulate USB drop: same object the engine still holds, but the fd is dead.
    original.write_error = OSError(errno.ENODEV, "No such device")
    cm108.set_ptt(False)

    assert cm108.device is replacement
    assert cm108.device.path == "/dev/hidraw4"
    assert cm108._gone is False
    assert replacement.written_reports == [bytes([0x00, 0x00, GPIO1, GPIO1 | GPIO3, 0x00])]


def test_epipe_also_triggers_hidraw_reopen():
    dead = FakeHidrawDevice(write_error=OSError(errno.EPIPE, "Broken pipe"))
    alive = FakeHidrawDevice()
    cm108 = CM108Interface(
        dead,
        find=lambda: [{"path": "/dev/hidraw2", "vendor": 0x0D8C, "product": 0x000C, "name": "x"}],
        open_device=lambda path: alive,
    )
    cm108.set_ptt(False)
    assert cm108.device is alive
    assert alive.written_reports == [bytes([0x00, 0x00, 0x00, GPIO3, 0x00])]


def test_cos_read_reopens_hidraw_after_disconnect():
    dead = FakeHidrawDevice()
    alive = FakeHidrawDevice(buttons=VOL_DN)

    def boom(length: int) -> bytes:
        raise OSError(errno.ENODEV, "No such device")

    dead.get_input_report = boom  # type: ignore[method-assign]
    cm108 = CM108Interface(
        dead,
        find=lambda: [{"path": "/dev/hidraw1", "vendor": 0x0D8C, "product": 0x013C, "name": "x"}],
        open_device=lambda path: alive,
    )
    assert cm108.read_cos() is True
    assert cm108.device is alive


def test_other_write_errors_still_raise():
    device = FakeHidrawDevice(write_error=OSError(errno.EIO, "Input/output error"))
    with pytest.raises(OSError) as raised:
        CM108Interface(device).set_ptt(False)
    assert raised.value.errno == errno.EIO


def test_ensure_open_reopens_without_waiting_for_ptt(tmp_path):
    dead = FakeHidrawDevice(write_error=OSError(errno.ENODEV, "No such device"))
    dead.path = str(tmp_path / "missing-hidraw")
    alive = FakeHidrawDevice()
    alive.path = str(tmp_path / "hidraw-new")
    (tmp_path / "hidraw-new").write_text("")

    cm108 = CM108Interface(
        dead,
        find=lambda: [{"path": alive.path, "vendor": 0x0D8C, "product": 0x013C, "name": "x", "stable_id": "usb-a"}],
        open_device=lambda path: alive,
        stable_id="usb-a",
    )
    cm108._gone = True
    assert cm108.ensure_open() is True
    assert cm108.device is alive
    assert cm108.gone is False
    assert alive.written_reports  # GPIO state rewritten on reopen


def test_candidate_paths_prefer_matching_stable_id():
    device = FakeHidrawDevice()
    device.path = "/dev/hidraw1"
    found = [
        {"path": "/dev/hidraw9", "vendor": 0x0D8C, "product": 0x013C, "name": "other", "stable_id": "usb-other"},
        {"path": "/dev/hidraw4", "vendor": 0x0D8C, "product": 0x013C, "name": "mine", "stable_id": "usb-mine"},
    ]
    cm108 = CM108Interface(device, find=lambda: found, stable_id="usb-mine")
    assert cm108._candidate_paths() == ["/dev/hidraw1", "/dev/hidraw4", "/dev/hidraw9"]
