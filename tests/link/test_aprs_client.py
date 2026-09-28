from link.aprs_client import compute_passcode, format_position_report, format_status_report


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


def test_format_status_report_is_a_bare_comment_with_no_timestamp():
    assert format_status_report("Net Control Center") == ">Net Control Center"
