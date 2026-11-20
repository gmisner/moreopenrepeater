"""The SNTP client against a fake NTP server on a real UDP socket (RFC 4330): what it believes and what it throws away."""
import socket
import time

import pytest

import fake_ntp
from fake_ntp import FakeNtp, good_reply, packet, request_transmit
from api import time_sync as ts

LOCAL = "127.0.0.1"
QUICK = 0.25  # seconds to wait for an answer that never comes


def ask(server, **kwargs):
    kwargs.setdefault("timeout", QUICK)
    return ts.sntp_query(LOCAL, port=server.port, **kwargs)


def sending(build):
    """A server that answers every request with build(request)."""
    return FakeNtp(lambda data, sock, address: sock.sendto(build(data), address))


# -- a good answer -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("offset", [0.0, 5.0, -3.25, 3600.0])
def test_a_good_reply_gives_the_offset_the_server_is_ahead_by(offset):
    with FakeNtp(lambda d, s, a: s.sendto(good_reply(d, offset=offset), a)) as server:
        result = ask(server)
    assert result.ok and result.error is None
    assert result.offset == pytest.approx(offset, abs=0.05)  # positive: the server is ahead, so this clock is behind
    assert result.stratum == 2 and 0 <= result.delay < 0.1


def test_the_round_trip_leaves_out_the_time_the_server_spent_thinking():
    def slow(data, sock, address):
        t2 = time.time()
        time.sleep(0.25)  # the server sits on the request, then stamps an honest transmit time
        sock.sendto(packet(stratum=2, originate=request_transmit(data), receive=t2, transmit=time.time()), address)

    with FakeNtp(slow) as server:
        result = ask(server, timeout=1.5)
    assert result.ok and result.delay < 0.1  # (t4 - t1) - (t3 - t2): the quarter second is the server's, not the network's
    assert result.offset == pytest.approx(0.0, abs=0.1)


def test_the_request_is_a_client_packet_with_a_fresh_transmit_timestamp_each_time():
    with FakeNtp() as server:
        ask(server)
        ask(server)
        first, second = server.requests
    for data in (first, second):
        assert len(data) == 48 and data[0] == 0x23  # no leap warning, version 4, mode 3 (client)
        seconds, _fraction = request_transmit(data)
        assert seconds != 0 and abs((seconds - fake_ntp.NTP_EPOCH) - time.time()) < 5
    assert request_transmit(first) != request_transmit(second)  # the low bits are random: an off-path guess has to match them


@pytest.mark.parametrize("stratum", [1, 2, 15])
def test_any_valid_stratum_is_accepted(stratum):
    with FakeNtp(lambda d, s, a: s.sendto(good_reply(d, stratum=stratum), a)) as server:
        assert ask(server).ok


def test_a_server_that_adds_extension_fields_or_a_mac_is_still_read():
    with sending(lambda d: good_reply(d) + b"\x00" * 20) as server:
        assert ask(server).ok


def test_version_3_replies_are_accepted():
    with sending(lambda d: good_reply(d, version=3)) as server:
        assert ask(server).ok


# -- what must not be believed ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("code", [b"RATE", b"DENY", b"RSTR"])
def test_a_kiss_o_death_is_a_refusal_not_a_time(code):
    refid = int.from_bytes(code, "big")
    with sending(lambda d: packet(stratum=0, refid=refid, originate=request_transmit(d), receive=time.time(), transmit=time.time())) as server:
        result = ask(server)
    assert not result.ok and result.error == "kiss" and result.offset is None


def test_a_kiss_o_death_with_a_plausible_time_in_it_still_gives_no_offset():
    """The time stamps in it look fine. The stratum is what says to stop."""
    with sending(lambda d: good_reply(d, stratum=0)) as server:
        result = ask(server)
    assert not result.ok and result.error == "kiss" and result.offset is None


@pytest.mark.parametrize("stratum", [16, 17, 255])
def test_a_server_that_is_not_synchronized_is_not_a_reference(stratum):
    with sending(lambda d: good_reply(d, stratum=stratum)) as server:
        result = ask(server)
    assert not result.ok and result.error == "unsynchronized" and result.offset is None


def test_the_leap_indicator_alarm_means_not_synchronized_even_with_a_good_stratum():
    with sending(lambda d: good_reply(d, stratum=2, leap=3)) as server:
        result = ask(server)
    assert not result.ok and result.error == "unsynchronized"


def test_a_reply_that_echoes_the_wrong_originate_timestamp_is_not_believed_and_the_wait_goes_on():
    """An answer to some other question (stale, or forged by someone who did not see ours) must not set the verdict."""
    def forged(data):
        return packet(stratum=2, originate=(1234, 5678), receive=time.time() + 100, transmit=time.time() + 100)

    started = time.monotonic()
    with sending(forged) as server:
        result = ask(server)
    assert not result.ok and result.error == "bad_reply" and result.offset is None
    assert time.monotonic() - started >= QUICK * 0.9  # it kept waiting for a real answer until the timeout


def test_the_originate_timestamp_must_match_exactly_not_nearly():
    def off_by_one_bit(data):
        seconds, fraction = request_transmit(data)
        return packet(stratum=2, originate=(seconds, fraction ^ 1), receive=time.time(), transmit=time.time())

    with sending(off_by_one_bit) as server:
        assert ask(server).error == "bad_reply"


def test_a_zero_originate_timestamp_is_not_an_answer():
    with sending(lambda d: packet(stratum=2, originate=(0, 0), receive=time.time(), transmit=time.time())) as server:
        assert ask(server).error == "bad_reply"


def test_a_real_reply_after_a_forged_one_is_still_used():
    def both(data, sock, address):
        sock.sendto(packet(stratum=2, originate=(1, 2), receive=time.time() + 999, transmit=time.time() + 999), address)
        sock.sendto(good_reply(data, offset=2.0), address)

    with FakeNtp(both) as server:
        result = ask(server, timeout=1.0)
    assert result.ok and result.offset == pytest.approx(2.0, abs=0.05)  # the forged +999 s was not the verdict


def test_a_reply_from_the_wrong_address_is_ignored():
    stranger = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    stranger.bind((LOCAL, 0))  # same host, another port: not the server that was asked

    def from_elsewhere(data, sock, address):
        stranger.sendto(good_reply(data, offset=500.0), address)

    try:
        with FakeNtp(from_elsewhere) as server:
            result = ask(server)
    finally:
        stranger.close()
    assert not result.ok and result.error == "bad_reply" and result.offset is None


@pytest.mark.parametrize("mode", [3, 5, 1, 0])
def test_only_a_server_mode_reply_counts(mode):
    with sending(lambda d: good_reply(d, mode=mode)) as server:
        assert ask(server).error == "bad_reply"


@pytest.mark.parametrize("version", [0, 1, 2, 5, 7])
def test_an_odd_protocol_version_is_ignored(version):
    with sending(lambda d: good_reply(d, version=version)) as server:
        assert ask(server).error == "bad_reply"


def test_a_server_that_stamps_zero_times_is_not_believed():
    with sending(lambda d: packet(stratum=2, originate=request_transmit(d), receive=None, transmit=None)) as server:
        assert ask(server).error == "bad_reply"


def test_a_server_that_claims_to_have_sent_before_it_received_is_not_believed():
    now = time.time()
    with sending(lambda d: packet(stratum=2, originate=request_transmit(d), receive=now + 5, transmit=now)) as server:
        assert ask(server).error == "bad_reply"


def test_a_server_whose_processing_time_is_longer_than_the_whole_round_trip_is_not_believed():
    """(t3 - t2) cannot exceed what the round trip took: the delay would be negative."""
    with sending(lambda d: packet(stratum=2, originate=request_transmit(d), receive=time.time(), transmit=time.time() + 30)) as server:
        assert ask(server).error == "bad_reply"


# -- no answer, or no sense -----------------------------------------------------------------------------------------------------


def test_a_server_that_never_answers_ends_in_the_timeout_and_not_later():
    started = time.monotonic()
    with FakeNtp(lambda d, s, a: None) as server:
        result = ask(server, timeout=0.3)
    elapsed = time.monotonic() - started
    assert not result.ok and result.error == "timeout"
    assert 0.25 <= elapsed < 1.5


@pytest.mark.parametrize("junk", [b"", b"x", b"\x23" + b"\x00" * 46, b"\xff" * 200, bytes(range(48))])
def test_a_short_or_garbage_packet_is_ignored(junk):
    with sending(lambda d: junk) as server:
        result = ask(server)
    assert not result.ok and result.error == "bad_reply" and result.offset is None


def test_nothing_listening_is_a_timeout_or_a_network_error_never_an_exception():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind((LOCAL, 0))
    port = probe.getsockname()[1]
    probe.close()  # nobody on that port now
    result = ts.sntp_query(LOCAL, port=port, timeout=0.3)
    assert not result.ok and result.error in ("timeout", "network")


def test_a_name_that_does_not_resolve_is_a_resolve_error():
    def no_such_name(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    result = ts.sntp_query("nope.example", resolver=no_such_name, timeout=0.3)
    assert not result.ok and result.error == "resolve"


def test_a_lookup_that_returns_nothing_is_a_resolve_error():
    assert ts.sntp_query("x.example", resolver=lambda *a, **k: [], timeout=0.3).error == "resolve"


def test_an_address_the_network_will_not_send_to_is_a_network_error():
    resolver = lambda *a, **k: [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", (LOCAL, 0))]  # port 0 cannot be sent to
    assert ts.sntp_query("x.example", resolver=resolver, timeout=0.3).error == "network"


def test_the_second_address_is_tried_when_the_first_does_not_answer():
    with FakeNtp() as live:
        dead = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dead.bind((LOCAL, 0))
        resolver = lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", (LOCAL, dead.getsockname()[1])),
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", (LOCAL, live.port)),
        ]
        try:
            result = ts.sntp_query("two.example", resolver=resolver, timeout=0.3)
        finally:
            dead.close()
    assert result.ok


def test_a_kiss_o_death_ends_it_without_trying_the_next_address():
    refused = FakeNtp(lambda d, s, a: s.sendto(good_reply(d, stratum=0), a))
    other = FakeNtp()
    with refused, other:
        resolver = lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", (LOCAL, refused.port)),
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", (LOCAL, other.port)),
        ]
        result = ts.sntp_query("two.example", resolver=resolver, timeout=0.3)
        assert result.error == "kiss" and other.requests == []  # asking the next address after being told to stop would be the wrong thing


def test_at_most_two_addresses_are_tried():
    servers = [FakeNtp(lambda d, s, a: None) for _ in range(3)]
    for server in servers:
        server._thread.start()
    try:
        resolver = lambda *a, **k: [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", (LOCAL, s.port)) for s in servers]
        ts.sntp_query("many.example", resolver=resolver, timeout=0.2)
        time.sleep(0.1)
        assert [len(s.requests) for s in servers] == [1, 1, 0]
    finally:
        for server in servers:
            server._stop.set()
            server._thread.join(1)
            server.sock.close()


# -- time stamps ------------------------------------------------------------------------------------------------------------------


def test_timestamps_round_trip_and_survive_the_ntp_era_change_in_2036():
    now = 2085978496 - 10  # a few seconds before the 32-bit NTP seconds wrap (2036-02-07)
    seconds, fraction = ts._to_ntp(now + 0.5)
    assert ts._from_ntp(seconds, fraction, now) == pytest.approx(now + 0.5, abs=1e-6)
    after = ts._to_ntp(now + 20.25)  # the server's clock is already past the wrap, ours is not
    assert after[0] < 100
    assert ts._from_ntp(after[0], after[1], now) == pytest.approx(now + 20.25, abs=1e-6)
    assert ts._from_ntp(*ts._to_ntp(1_700_000_000.75), 1_700_000_000.0) == pytest.approx(1_700_000_000.75, abs=1e-6)
