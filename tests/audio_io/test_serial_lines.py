import termios

import pytest

from audio_io import serial_lines
from audio_io.serial_lines import SerialLine, find_serial_ports


class FakePort:
    def __init__(self, bits=termios.TIOCM_DTR | termios.TIOCM_RTS):
        self.bits = bits  # a tty comes up with DTR and RTS raised
        self.hangup_on_close = True
        self.closed = False

    def get(self):
        return self.bits

    def assert_bits(self, bits):
        self.bits |= bits

    def clear_bits(self, bits):
        self.bits &= ~bits

    def set_hangup_on_close(self, on):
        self.hangup_on_close = on

    def close(self):
        self.closed = True


def test_active_high_ptt_drops_rts_on_open_and_asserts_it_to_key():
    port = FakePort()
    ptt = SerialLine(port, "rts", output=True, active_low=False)

    assert not port.bits & termios.TIOCM_RTS
    assert port.bits & termios.TIOCM_DTR  # other lines left alone
    assert port.hangup_on_close  # a crash drops RTS: unkeyed

    ptt.write(True)
    assert port.bits & termios.TIOCM_RTS
    ptt.write(False)
    assert not port.bits & termios.TIOCM_RTS


def test_active_low_ptt_holds_the_line_up_and_keeps_it_up_after_close():
    port = FakePort(bits=0)
    ptt = SerialLine(port, "dtr", output=True, active_low=True)

    assert port.bits & termios.TIOCM_DTR
    assert not port.hangup_on_close  # dropping DTR would key the radio

    ptt.write(True)
    assert not port.bits & termios.TIOCM_DTR


@pytest.mark.parametrize("line, bit", [("cts", termios.TIOCM_CTS), ("dsr", termios.TIOCM_DSR), ("dcd", termios.TIOCM_CAR)])
def test_cos_reads_the_chosen_input_line_with_polarity(line, bit):
    port = FakePort(bits=0)
    high = SerialLine(port, line, output=False, active_low=False)
    low = SerialLine(port, line, output=False, active_low=True)
    assert port.hangup_on_close  # inputs don't touch it

    assert (high.read(), low.read()) == (False, True)
    port.bits = bit
    assert (high.read(), low.read()) == (True, False)


def test_lines_must_be_the_right_direction():
    with pytest.raises(ValueError):
        SerialLine(FakePort(), "cts", output=True, active_low=False)
    with pytest.raises(ValueError):
        SerialLine(FakePort(), "rts", output=False, active_low=False)


def test_close_closes_the_port():
    port = FakePort()
    SerialLine(port, "cts", output=False, active_low=False).close()
    assert port.closed


def test_find_serial_ports_lists_by_id_names_first_without_duplicates(monkeypatch):
    globs = {
        "/dev/serial/by-id/*": ["/dev/serial/by-id/usb-FTDI_FT232R-if00-port0"],
        "/dev/ttyUSB*": ["/dev/ttyUSB0", "/dev/ttyUSB1"],
        "/dev/ttyACM*": ["/dev/ttyACM0"],
    }
    monkeypatch.setattr(serial_lines.glob, "glob", lambda pattern: globs.get(pattern, []))
    monkeypatch.setattr(serial_lines.os.path, "realpath", lambda path: "/dev/ttyUSB0" if "by-id" in path else path)

    assert find_serial_ports() == [
        {"path": "/dev/serial/by-id/usb-FTDI_FT232R-if00-port0", "target": "/dev/ttyUSB0"},
        {"path": "/dev/ttyACM0", "target": "/dev/ttyACM0"},
        {"path": "/dev/ttyUSB1", "target": "/dev/ttyUSB1"},
    ]
