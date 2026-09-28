from audio_io.cm108 import CM108Interface, GPIO1, GPIO2, GPIO3, GPIO4


class FakeHidrawDevice:
    """In-memory stand-in for /dev/hidrawN so CM108Interface's bit-packing
    logic can be tested without real hardware."""

    def __init__(self, initial_read_report: bytes = bytes(5)) -> None:
        self.written_reports: list[bytes] = []
        self._next_read_report = initial_read_report

    def write_report(self, report: bytes) -> None:
        self.written_reports.append(report)

    def read_report(self, length: int) -> bytes:
        return self._next_read_report[:length]

    def set_next_read_report(self, report: bytes) -> None:
        self._next_read_report = report


def test_set_ptt_active_writes_data_before_mask_with_pin_bit_set():
    device = FakeHidrawDevice()
    cm108 = CM108Interface(device=device)

    cm108.set_ptt(active=True)

    assert device.written_reports == [bytes([0x00, 0x00, GPIO3, GPIO3, 0x00])]


def test_set_ptt_inactive_writes_zero_data_with_pin_still_marked_as_output():
    device = FakeHidrawDevice()
    cm108 = CM108Interface(device=device)

    cm108.set_ptt(active=False)

    assert device.written_reports == [bytes([0x00, 0x00, 0x00, GPIO3, 0x00])]


def test_read_cos_reports_true_when_pin_bit_set_in_first_byte():
    device = FakeHidrawDevice()
    device.set_next_read_report(bytes([GPIO4, 0, 0, 0, 0]))
    cm108 = CM108Interface(device=device)

    assert cm108.read_cos() is True


def test_read_cos_reports_false_when_pin_bit_clear():
    device = FakeHidrawDevice()
    device.set_next_read_report(bytes([0, 0, 0, 0, 0]))
    cm108 = CM108Interface(device=device)

    assert cm108.read_cos() is False


def test_read_cos_ignores_unrelated_bits_in_first_byte():
    device = FakeHidrawDevice()
    device.set_next_read_report(bytes([GPIO3, 0, 0, 0, 0]))  # PTT bit set, not COS
    cm108 = CM108Interface(device=device)

    assert cm108.read_cos() is False


def test_custom_pin_assignment_is_respected():
    device = FakeHidrawDevice()
    cm108 = CM108Interface(device=device, ptt_pin=0x01, cos_pin=0x02)

    cm108.set_ptt(active=True)
    device.set_next_read_report(bytes([0x02, 0, 0, 0, 0]))

    assert device.written_reports == [bytes([0x00, 0x00, 0x01, 0x01, 0x00])]
    assert cm108.read_cos() is True


def test_set_gpio_on_an_auxiliary_pin_not_used_for_ptt_or_cos():
    device = FakeHidrawDevice()
    cm108 = CM108Interface(device=device)

    cm108.set_gpio(GPIO1, active=True)

    assert device.written_reports == [bytes([0x00, 0x00, GPIO1, GPIO1, 0x00])]


def test_read_gpio_on_an_auxiliary_pin_not_used_for_ptt_or_cos():
    device = FakeHidrawDevice()
    device.set_next_read_report(bytes([GPIO2, 0, 0, 0, 0]))
    cm108 = CM108Interface(device=device)

    assert cm108.read_gpio(GPIO2) is True
    assert cm108.read_gpio(GPIO1) is False


def test_set_ptt_is_a_thin_wrapper_over_set_gpio():
    device = FakeHidrawDevice()
    cm108 = CM108Interface(device=device)

    cm108.set_ptt(active=True)

    assert device.written_reports == [bytes([0x00, 0x00, cm108.ptt_pin, cm108.ptt_pin, 0x00])]
