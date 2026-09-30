import pytest

from audio_io.cm108 import GPIO1, GPIO3, GPIO4, VOL_DN, VOL_UP, CM108Interface, _hidiocginput, gpio_bit


class FakeHidrawDevice:
    """In-memory stand-in for /dev/hidrawN so CM108Interface's bit-packing
    logic can be tested without real hardware."""

    def __init__(self, buttons: int = 0, gpio: int = 0) -> None:
        self.written_reports: list[bytes] = []
        self.buttons = buttons
        self.gpio = gpio

    def write_report(self, report: bytes) -> None:
        self.written_reports.append(report)

    def get_input_report(self, length: int) -> bytes:
        return bytes([self.buttons, self.gpio, 0, 0])[:length]


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
