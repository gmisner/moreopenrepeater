import struct

from audio_io.pi_gpio import _GET_LINE, line_flags, line_request


def test_line_request_matches_the_kernel_struct():
    request = line_request(17, line_flags(output=True, active_low=False))
    assert len(request) == 592  # sizeof(struct gpio_v2_line_request)
    assert struct.unpack_from("=I", request, 0)[0] == 17  # offsets[0]
    assert request[256:288].rstrip(b"\0") == b"moreopenrepeater"  # consumer
    assert struct.unpack_from("=Q", request, 288)[0] == 1 << 3  # config.flags: output
    assert struct.unpack_from("=I", request, 560)[0] == 1  # num_lines
    assert _GET_LINE == 0xC250B407  # GPIO_V2_GET_LINE_IOCTL


def test_inputs_pull_toward_their_inactive_level():
    active_low = line_flags(output=False, active_low=True)
    active_high = line_flags(output=False, active_low=False)
    assert active_low == (1 << 1) | (1 << 2) | (1 << 8)  # active low, input, pull-up
    assert active_high == (1 << 2) | (1 << 9)  # input, pull-down


def test_outputs_get_no_pull():
    assert line_flags(output=True, active_low=True) == (1 << 1) | (1 << 3)
