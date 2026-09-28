from link.aprs_client import compute_passcode, format_frequency_comment, format_position_report, format_status_report


def test_compute_passcode_matches_the_well_known_n0call_reference_value():
    # N0CALL -> 13023 is the canonical example cited throughout APRS tooling
    # (aprslib, Direwolf docs, etc); hand-verified against the algorithm too.
    assert compute_passcode("N0CALL") == 13023


def test_compute_passcode_is_case_insensitive():
    assert compute_passcode("n0call") == compute_passcode("N0CALL")


def test_compute_passcode_ignores_ssid():
    assert compute_passcode("N0CALL-9") == compute_passcode("N0CALL")


def test_compute_passcode_is_within_15_bit_range():
    assert 0 <= compute_passcode("W1AW") <= 0x7FFF


def test_format_position_report_encodes_lat_lon_and_comment():
    packet = format_position_report(lat=49.0583, lon=-72.0292, comment="Test repeater")

    assert packet == "!4903.50N/07201.75W-Test repeater"


def test_format_position_report_handles_southern_and_eastern_hemispheres():
    packet = format_position_report(lat=-33.87, lon=151.21, comment="")

    assert packet.startswith("!3352.20S")
    assert "/15112.60E-" in packet


def test_format_position_report_uses_the_given_symbol():
    assert format_position_report(49.0583, -72.0292, "", "/", "r") == "!4903.50N/07201.75Wr"


def test_frequency_comment_matches_the_freq_spec_examples():
    # Examples from aprs.org/info/freqspec.txt.
    assert format_frequency_comment(146.94, 100.0, -0.6) == "146.940MHz T100 -060"
    assert format_frequency_comment(442.44, 107.2, -5.0) == "442.440MHz T107 -500"


def test_frequency_comment_truncates_tones_to_whole_hertz():
    assert format_frequency_comment(147.0, 67.0) == "147.000MHz T067"
    assert format_frequency_comment(147.0, 88.5) == "147.000MHz T088"


def test_frequency_comment_frequency_is_always_ten_bytes():
    assert format_frequency_comment(146.52) == "146.520MHz"
    assert format_frequency_comment(53.03) == "053.030MHz"
    assert format_frequency_comment(1282.5) == "1282.50MHz"


def test_frequency_comment_offset_without_tone_uses_toff():
    assert format_frequency_comment(146.94, offset_mhz=-0.6) == "146.940MHz Toff -060"


def test_frequency_comment_zero_offset_forces_simplex():
    assert format_frequency_comment(146.52, 100.0, 0.0) == "146.520MHz T100 -000"


def test_format_status_report_is_a_bare_comment_with_no_timestamp():
    assert format_status_report("Net Control Center") == ">Net Control Center"
