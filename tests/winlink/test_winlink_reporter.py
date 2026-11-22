"""The Winlink reporter that runs inside the gateway and modem containers (ADR 0006, sessions and counters).

The fixtures are real output, captured from LinBPQ 6.0.26.1 (the pinned build) and Dire Wolf 1.8.1 running in the images from this
repository on a bench: LinBPQ against a stand-in packet modem and a stand-in Winlink CMS, Dire Wolf fed test audio. They are not from
the air. What matters most here is that a counter is right, that nothing but whitelisted facts leave the container, and that no
message text, password or free-form text from a remote station is ever read into the snapshot."""
import importlib.util
import json
import os
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "docker" / "winlink-reporter.py"
FIXTURES = Path(__file__).parent / "fixtures"

spec = importlib.util.spec_from_file_location("winlink_reporter", SCRIPT)
rep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rep)

ADIF = (FIXTURES / "bpq_cms_adif_202611.adi").read_bytes().decode("latin-1")
CONNECTS = (FIXTURES / "ConnectLog_261121.log").read_bytes().decode("latin-1")
DIREWOLF = (FIXTURES / "direwolf_stdout.txt").read_bytes()


def utc(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


# -- the session records LinBPQ writes -----------------------------------------------------------------------------------


def test_the_two_real_sessions_are_read_with_their_counts_and_how_they_ended():
    records, consumed = rep.parse_adif(ADIF)
    assert consumed == len(ADIF) and len(records) == 2
    done, early = records
    assert done["callsign"] == "N0CALL-1"
    assert done["end"] == utc("2026-11-21 01:29:59") and done["duration_s"] == 7 and done["start"] == utc("2026-11-21 01:29:52")
    # The station proposed one message and was sent one. LinBPQ's own "Sent" is what went up to Winlink, so it is "in" here.
    assert (done["messages_in"], done["messages_out"], done["bytes_in"], done["bytes_out"]) == (1, 1, 120, 300)
    assert done["outcome"] == "completed"
    assert early["end"] == utc("2026-11-21 01:33:07") and early["duration_s"] == 14
    assert (early["messages_in"], early["messages_out"]) == (0, 0) and early["outcome"] == "ended_early"  # the link dropped mid-exchange


def test_a_record_is_taken_only_when_it_is_whole_and_the_rest_waits_for_the_next_read():
    cut = ADIF.index("<eor>", ADIF.index("<eor>") + 5) - 10  # inside the second record
    records, consumed = rep.parse_adif(ADIF[:cut])
    assert len(records) == 1 and consumed == ADIF.index("<eor>") + len("<eor>\r\n")
    more, consumed_again = rep.parse_adif(ADIF[consumed:])
    assert len(more) == 1 and consumed_again == len(ADIF) - consumed  # the rest, header not needed again


def test_the_file_header_is_not_a_record_and_a_file_with_only_a_header_has_none():
    records, consumed = rep.parse_adif(ADIF[: ADIF.index("<eoh>") + 7])
    assert records == [] and consumed == ADIF.index("<eoh>") + 7


def record(call="W1AW-10", comment=None, date="20261121", on="120000", off="120100"):
    comment = comment if comment is not None else "0|2026-11-21 12:01:00|BPQ32|6.0.26.1|CMS|W1AW|%s|[SID]|Packet 1200|0|0|0|FQ|2|3|400|500|60|FN31PR|FN31PR" % call
    return (
        f"<call:{len(call)}>{call}<qso_date:8>{date}<time_on:6>{on}<time_off:6>{off}<mode:3>PKT<gridsquare:6>FN31PR<band:0><freq:0>"
        f"<comment:{len(comment)}>{comment}<eor>\r\n"
    )


def one(text):
    records, _ = rep.parse_adif(text)
    assert len(records) == 1, records
    return records[0]


def test_a_station_cannot_move_the_numbers_by_putting_bars_in_its_own_identification():
    # The comment is joined with "|", and the station's SID sits in the middle. Counting from the end keeps the numbers right
    # however many bars the SID holds.
    hostile = "0|2026-11-21 12:01:00|BPQ32|6.0.26.1|CMS|W1AW|W1AW-10|[A|B|C|D]|Packet 1200|0|0|0|FQ|2|3|400|500|60|FN31PR|FN31PR"
    got = one(record(comment=hostile))
    assert (got["messages_in"], got["messages_out"], got["bytes_in"], got["bytes_out"], got["duration_s"]) == (2, 3, 400, 500, 60)
    assert got["outcome"] == "completed"


def test_bars_in_the_locator_it_typed_do_not_move_them_either():
    hostile = "0|2026-11-21 12:01:00|BPQ32|6.0.26.1|CMS|W1AW|W1AW-10|[SID]|Packet 1200|0|0|0|FQ|2|3|400|500|60|F|N|3|FN31PR"
    got = one(record(comment=hostile))
    assert (got["messages_in"], got["messages_out"], got["bytes_in"], got["bytes_out"], got["duration_s"]) == (2, 3, 400, 500, 60)


@pytest.mark.parametrize("call, expected", [
    ("N0CALL-1", "N0CALL-1"), ("k1abc", "K1ABC"), ("W1AW-15", "W1AW-15"),
    ("<script>", None), ("W1AW 10", None), ("TOOLONGCALL-1", None), ("A", None), ("W1AW-99", None), ("", None),
])
def test_only_something_shaped_like_a_callsign_is_kept(call, expected):
    assert one(record(call=call))["callsign"] == expected


def test_what_cannot_be_read_never_becomes_a_count_or_an_error():
    broken = one(record(comment="garbage that is not the comment layout"))
    assert (broken["messages_in"], broken["messages_out"], broken["bytes_in"], broken["bytes_out"]) == (0, 0, 0, 0)
    assert broken["outcome"] == "unknown"
    silly = one(record(comment="0|2026-11-21 12:01:00|B|6|CMS|W|W1AW-10|[S]|P|0|0|0|FQ|-5|7|abc|-1|60|L|L"))
    assert (silly["messages_in"], silly["messages_out"], silly["bytes_in"], silly["bytes_out"]) == (0, 0, 0, 0)  # not numbers: not counts
    assert silly["outcome"] == "unknown" and silly["duration_s"] == 60  # the length falls back to the start and end times
    huge = one(record(comment="0|2026-11-21 12:01:00|B|6|CMS|W|W1AW-10|[S]|P|0|0|0|FQ|99999999999|2|3|4|60|L|L"))
    assert huge["messages_in"] == rep.MAX_COUNT and huge["messages_out"] == 2  # a huge number is held down, not trusted
    text = "<call:3>abc<eor>\r\n<qso_date:8>zzzzzzzz<eor>\r\n"
    nothing, consumed = rep.parse_adif(text)
    assert nothing == [] and consumed == len(text)  # records with no usable time are skipped, and not read again


def test_a_session_crossing_midnight_ends_on_the_next_day():
    got = one(record(comment="x", on="235950", off="000030"))  # no usable comment: the tags decide
    assert got["end"] == utc("2026-11-22 00:00:30") and got["start"] == utc("2026-11-21 23:59:50") and got["duration_s"] == 40


def test_the_one_with_no_start_time_is_dated_from_its_end_and_length():
    # LinBPQ writes 1970 as the start when no line ever reached Winlink. The comment's end time and length are still good.
    got = one(record(date="19700101", on="000000", comment="0|2026-11-21 12:01:00|B|6|CMS|W|W1AW-10|[S]|P|0|0|0|FQ|0|0|0|0|5|L|L"))
    assert got["end"] == utc("2026-11-21 12:01:00") and got["start"] == got["end"] - 5


# -- the connect log -----------------------------------------------------------------------------------------------------


def test_only_radio_connections_are_counted_from_the_connect_log():
    got = [rep.parse_connect_line(line) for line in CONNECTS.splitlines()]
    # The first is a TELNET login (someone at the sysop prompt), not a station on the air. The rest are seconds into the UTC day.
    assert got == [None, 5312, 5392, 5573]


@pytest.mark.parametrize("line", ["", "garbage", "25:00:00 Call from A to B Mode AX.25", "01:02:03 Call from A to B Mode NETROM",
                                  "01:02:03 something else", "01:02 Call from A to B Mode AX.25"])
def test_other_lines_in_the_connect_log_count_for_nothing(line):
    assert rep.parse_connect_line(line) is None


# -- the counters ---------------------------------------------------------------------------------------------------------


DAY = datetime(2026, 11, 21, tzinfo=timezone.utc).timestamp()


def tracker(**kwargs):
    t = rep.GatewayTracker(**kwargs)
    t.feed_adif(ADIF)
    t.feed_connects(DAY, CONNECTS)
    return t


def test_the_counters_add_up_over_the_two_real_sessions_and_the_connect_that_never_reached_winlink():
    t = tracker()
    snap = t.snapshot(DAY + 4 * 3600)  # hours later: nothing is still in progress
    c = snap["counters"]
    assert c["sessions"] == 2 and c["incomplete"] == 1
    assert (c["messages_in"], c["messages_out"], c["bytes_in"], c["bytes_out"]) == (1, 1, 120, 300)
    assert c["connects"] == 3 and c["failed_connects"] == 1  # the 01:28:32 connect has no session after it
    assert c["last_session_end"] == utc("2026-11-21 01:33:07")
    assert [s["callsign"] for s in snap["sessions"]] == ["N0CALL-1", "N0CALL-1"]
    assert [s["end"] for s in snap["sessions"]] == [utc("2026-11-21 01:29:59"), utc("2026-11-21 01:33:07")]  # oldest first


def test_sessions_that_ended_early_are_counted_apart_from_the_ones_that_finished():
    t = rep.GatewayTracker()
    done = "0|2026-11-21 12:0%d:00|B|6|CMS|W|W1AW-10|[S]|P|0|0|0|FQ|1|0|10|0|30|L|L"
    early = "0|2026-11-21 12:0%d:00|B|6|CMS|W|W1AW-10|[S]|P|0|0|0|FS|0|0|0|0|30|L|L"
    for n, comment in enumerate([done, done, early, done], start=1):
        t.feed_adif(record(comment=comment % n, on=f"12{n:02d}00", off=f"12{n:02d}30"))
    c = t.snapshot(DAY + 86400)["counters"]
    assert c["sessions"] == 4 and c["incomplete"] == 1  # three finished, one did not: not the other way round
    assert [s["outcome"] for s in t.snapshot(DAY + 86400)["sessions"]] == ["completed", "completed", "ended_early", "completed"]


def test_a_connect_is_not_called_failed_while_a_session_may_still_be_running():
    t = rep.GatewayTracker(grace_seconds=1800)
    t.feed_connects(DAY, "01:28:32 Call from N0CALL-1 to W1AW-10 Mode AX.25\r\n")
    at = DAY + 1 * 3600 + 28 * 60 + 32
    assert t.snapshot(at + 1799)["counters"]["failed_connects"] == 0  # still inside the grace time
    assert t.snapshot(at + 1801)["counters"]["failed_connects"] == 1
    assert t.snapshot(at + 9000)["counters"]["failed_connects"] == 1  # counted once, however often it is asked


def test_a_session_that_ends_after_the_grace_time_still_clears_its_own_connect():
    t = rep.GatewayTracker(grace_seconds=60)
    t.feed_connects(DAY, "01:29:52 Call from W1AW-10 to N0CALL-1 Mode AX.25\r\n")
    t.feed_adif(one_record_text("N0CALL-1", "20261121", "012952", "013059", 67))
    assert t.snapshot(DAY + 7200)["counters"]["failed_connects"] == 0 and t.snapshot(DAY + 7200)["counters"]["sessions"] == 1


def one_record_text(call, date, on, off, seconds):
    comment = f"0|2026-11-21 {off[:2]}:{off[2:4]}:{off[4:]}|B|6|CMS|W|{call}|[S]|P|0|0|0|FQ|1|0|10|0|{seconds}|L|L"
    return record(call=call, comment=comment, date=date, on=on, off=off)


def test_each_session_is_counted_exactly_once_however_many_times_it_is_read():
    t = rep.GatewayTracker()
    text = one_record_text("N0CALL-1", "20261121", "120000", "120100", 60)
    t.feed_adif(text)
    t.feed_adif(text)  # the same bytes again (a reporter restarted over the same file)
    assert t.snapshot(DAY + 86400)["counters"]["sessions"] == 1


def test_the_session_list_is_bounded_but_the_counters_keep_counting():
    t = rep.GatewayTracker()
    for n in range(rep.MAX_SESSIONS + 25):
        second = n % 60
        minute = (n // 60) % 60
        t.feed_adif(one_record_text("N0CALL-1", "20261121", f"10{minute:02d}{second:02d}", f"10{minute:02d}{second:02d}", 1))
    snap = t.snapshot(DAY + 86400)
    assert len(snap["sessions"]) == rep.MAX_SESSIONS and snap["counters"]["sessions"] == rep.MAX_SESSIONS + 25
    assert snap["counters"]["messages_in"] == rep.MAX_SESSIONS + 25  # every one carried one message in


def test_a_session_has_a_stable_id_that_tells_two_apart():
    t = tracker()
    ids = [s["id"] for s in t.snapshot(DAY + 4 * 3600)["sessions"]]
    assert len(set(ids)) == 2 and all(len(i) == 12 for i in ids)
    again = tracker().snapshot(DAY + 4 * 3600)["sessions"]
    assert ids == [s["id"] for s in again]  # the same record gives the same id on every read: events are logged once


def test_the_snapshot_carries_only_the_whitelisted_facts_and_nothing_a_station_typed():
    secret = "SECRET-STATION-TEXT"
    t = rep.GatewayTracker()
    t.feed_adif(record(comment=f"0|2026-11-21 12:01:00|B|6|CMS|W|W1AW-10|[{secret}]|P|0|0|0|FQ|1|1|2|3|60|{secret}|{secret}"))
    text = json.dumps(t.snapshot(DAY + 86400))
    assert secret not in text
    snap = json.loads(text)
    assert set(snap) == {"v", "kind", "updated", "started", "counters", "sessions"}
    assert set(snap["sessions"][0]) == {"id", "callsign", "start", "end", "duration_s", "messages_in", "messages_out", "bytes_in",
                                       "bytes_out", "outcome"}
    assert set(snap["counters"]) == {"sessions", "incomplete", "messages_in", "messages_out", "bytes_in", "bytes_out", "connects",
                                     "failed_connects", "last_session_end"}


# -- reading the files, incrementally ------------------------------------------------------------------------------------


def logs(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir(exist_ok=True)
    return folder


def test_a_scan_reads_only_the_session_and_connect_files_and_counts_each_record_once(tmp_path, monkeypatch):
    folder = logs(tmp_path)
    (folder / "BPQ_CMS_ADIF_202611.adi").write_bytes(ADIF.encode("latin-1"))
    (folder / "ConnectLog_261121.log").write_bytes(CONNECTS.encode("latin-1"))
    # What must never be read: the telnet log keeps the password a user typed, the CMS log is a protocol trace.
    (folder / "Telnet_261121.log").write_text("21/11/2026 01:23:29 1 172.18.0.4 Password=hunter2-REAL\n")
    (folder / "CMSAccess_20261121.log").write_text("01:26:10 10 FC EM TESTMID1 120 90 0<cr>\n")
    (folder / "NodeDebuglog_261121.log").write_text("debug text\n")
    opened = []
    real = rep._read_chunk
    monkeypatch.setattr(rep, "_read_chunk", lambda path, offset, limit: (opened.append(Path(path).name), real(path, offset, limit))[1])
    watcher = rep.GatewayWatcher(folder, tmp_path / "gateway.json", clock=lambda: DAY + 4 * 3600)
    watcher.scan()
    watcher.scan()  # nothing new: nothing changes
    assert set(opened) == {"BPQ_CMS_ADIF_202611.adi", "ConnectLog_261121.log"}
    snap = json.loads((tmp_path / "gateway.json").read_text())
    assert snap["counters"]["sessions"] == 2 and snap["counters"]["connects"] == 3 and snap["counters"]["failed_connects"] == 1
    assert "hunter2" not in (tmp_path / "gateway.json").read_text() and "TESTMID" not in (tmp_path / "gateway.json").read_text()


def test_a_record_written_in_two_pieces_is_counted_once_when_it_is_whole(tmp_path):
    folder = logs(tmp_path)
    adif = folder / "BPQ_CMS_ADIF_202611.adi"
    data = ADIF.encode("latin-1")
    cut = data.index(b"<eor>") + 5 + 40  # inside the second record
    adif.write_bytes(data[:cut])
    watcher = rep.GatewayWatcher(folder, tmp_path / "gateway.json", clock=lambda: DAY + 86400)
    watcher.scan()
    assert json.loads((tmp_path / "gateway.json").read_text())["counters"]["sessions"] == 1
    with adif.open("ab") as handle:
        handle.write(data[cut:])
    watcher.scan()
    assert json.loads((tmp_path / "gateway.json").read_text())["counters"]["sessions"] == 2


def test_a_file_that_shrinks_is_read_again_from_the_start(tmp_path):
    folder = logs(tmp_path)
    adif = folder / "BPQ_CMS_ADIF_202611.adi"
    adif.write_bytes(ADIF.encode("latin-1"))
    watcher = rep.GatewayWatcher(folder, tmp_path / "gateway.json", clock=lambda: DAY + 86400)
    watcher.scan()
    adif.write_bytes(one_record_text("K1ABC", "20261121", "130000", "130100", 60).encode("latin-1"))  # replaced by a shorter file
    watcher.scan()
    assert json.loads((tmp_path / "gateway.json").read_text())["counters"]["sessions"] == 3


def test_the_connect_log_takes_its_date_from_its_name_and_a_new_day_is_a_new_file(tmp_path):
    folder = logs(tmp_path)
    (folder / "ConnectLog_261121.log").write_bytes(b"23:59:00 Call from W1AW-10 to N0CALL-1 Mode AX.25\r\n")
    (folder / "ConnectLog_261122.log").write_bytes(b"00:00:30 Call from W1AW-10 to K1ABC Mode AX.25\r\n")
    (folder / "ConnectLog_garbage.log").write_bytes(b"00:00:30 Call from W1AW-10 to K1ABC Mode AX.25\r\n")  # no date in its name: not read
    watcher = rep.GatewayWatcher(folder, tmp_path / "gateway.json", clock=lambda: utc("2026-11-22 05:00:00"))
    watcher.scan()
    c = json.loads((tmp_path / "gateway.json").read_text())["counters"]
    assert c["connects"] == 2 and c["failed_connects"] == 2


def test_an_unreadable_folder_or_file_does_not_stop_the_reporter(tmp_path):
    watcher = rep.GatewayWatcher(tmp_path / "does-not-exist", tmp_path / "gateway.json", clock=lambda: DAY)
    watcher.scan()  # no logs yet: it writes an empty picture instead of failing
    assert json.loads((tmp_path / "gateway.json").read_text())["counters"]["sessions"] == 0
    folder = logs(tmp_path)
    (folder / "BPQ_CMS_ADIF_202611.adi").write_bytes(ADIF.encode("latin-1"))
    (folder / "BPQ_CMS_ADIF_202611.adi").chmod(0)
    if os.geteuid() != 0:
        watcher = rep.GatewayWatcher(folder, tmp_path / "g2.json", clock=lambda: DAY)
        watcher.scan()
        assert json.loads((tmp_path / "g2.json").read_text())["counters"]["sessions"] == 0
        (folder / "BPQ_CMS_ADIF_202611.adi").chmod(0o600)


def test_a_huge_log_is_read_in_bounded_pieces(tmp_path):
    folder = logs(tmp_path)
    adif = folder / "BPQ_CMS_ADIF_202611.adi"
    records = [one_record_text("N0CALL-1", "20261121", f"12{n:02d}00", f"12{n:02d}59", 59) for n in range(20)]
    adif.write_bytes("".join(records).encode("latin-1"))
    watcher = rep.GatewayWatcher(folder, tmp_path / "gateway.json", clock=lambda: DAY + 86400, chunk_bytes=len(records[0]) * 5)
    watcher.scan()
    first = json.loads((tmp_path / "gateway.json").read_text())["counters"]["sessions"]
    assert 0 < first < 20  # one scan does not swallow the whole file
    for _ in range(10):
        watcher.scan()
    assert json.loads((tmp_path / "gateway.json").read_text())["counters"]["sessions"] == 20


# -- the snapshot file -----------------------------------------------------------------------------------------------------


def test_the_snapshot_is_replaced_whole_and_readable_by_the_dashboard(tmp_path):
    (tmp_path / "out").mkdir()
    target = tmp_path / "out" / "gateway.json"
    rep.write_snapshot(target, {"v": 1, "x": 1})
    rep.write_snapshot(target, {"v": 1, "x": 2})
    assert json.loads(target.read_text())["x"] == 2
    assert stat.S_IMODE(target.stat().st_mode) & 0o044  # the API reads it as another user of the same group or as the same uid
    assert [p.name for p in target.parent.iterdir()] == ["gateway.json"]  # no half-written file left beside it


def test_a_missing_output_folder_is_not_an_error_and_makes_no_folder():
    rep.write_snapshot(Path("/nonexistent-folder-for-test/gateway.json"), {"v": 1})  # must not raise
    assert not Path("/nonexistent-folder-for-test").exists()


# -- the modem's output ----------------------------------------------------------------------------------------------------


def test_the_real_modem_output_gives_three_frames_heard_and_one_sent():
    counter = rep.ModemCounter(clock=lambda: 1000.0)
    for line in DIREWOLF.splitlines():
        counter.feed(line)
    snap = counter.snapshot(1000.0)
    assert snap["counters"] == {"frames_heard": 3, "frames_sent": 1, "last_heard": 1000.0, "last_sent": 1000.0}


@pytest.mark.parametrize("line, heard, sent", [
    (b"[0.3] K1ABC>W1AW-10:hello<0x0a>", 1, 0),
    (b"[0] K1ABC>W1AW-10:hello", 1, 0),
    (b"[0.2.1] K1ABC>W1AW-10:hello", 1, 0),
    (b"[1.0] K1ABC>W1AW-10,WIDE1-1:hello", 1, 0),
    (b"[0L] W1AW-10>CQ:beacon", 0, 1),
    (b"[0H] W1AW-10>K1ABC:(RR)", 0, 1),
    (b"[0.dtmf] 123", 0, 0), (b"[0>is] x>y:z", 0, 0), (b"[0.speech] \"hi\"", 0, 0), (b"[0.morse] \"hi\"", 0, 0),
    (b"K1ABC audio level = 79(23/22)    _||||||__", 0, 0), (b"Ready to accept KISS TCP client application 1 on port 8001 ...", 0, 0),
    (b"", 0, 0), (b"[0.3]", 0, 0), (b"[x] a>b:c", 0, 0), (b" [0.3] K1ABC>W1AW-10:indented", 0, 0), (b"\xff\xfe[0.3] a>b:c", 0, 0),
])
def test_each_kind_of_modem_line_counts_as_what_it_is(line, heard, sent):
    counter = rep.ModemCounter()
    counter.feed(line)
    c = counter.snapshot(1.0)["counters"]
    assert (c["frames_heard"], c["frames_sent"]) == (heard, sent)


def test_the_modem_snapshot_keeps_no_text_of_any_frame():
    counter = rep.ModemCounter()
    counter.feed(b"[0.3] K1ABC>W1AW-10:PRIVATE-MESSAGE-TEXT")
    counter.feed(b"[0L] W1AW-10>K1ABC:ANOTHER-PRIVATE-TEXT")
    text = json.dumps(counter.snapshot(5.0))
    assert "PRIVATE" not in text and "K1ABC" not in text and "W1AW" not in text
    assert set(json.loads(text)) == {"v", "kind", "updated", "started", "counters"}


def test_dire_wolf_does_not_log_each_time_the_transmitter_is_keyed_so_none_is_reported():
    counter = rep.ModemCounter()
    counter.feed(b"[0L] W1AW-10>CQ:beacon")
    assert "keyed" not in json.dumps(counter.snapshot(1.0)).lower()


# -- running the modem ---------------------------------------------------------------------------------------------------------


FAKE_DIREWOLF = """#!/bin/sh
cat "$FIXTURE"
printf '[0.3] N9XYZ>W1AW-10:last frame'
exit 3
"""


def fake(tmp_path, body=FAKE_DIREWOLF):
    path = tmp_path / "direwolf"
    path.write_text(body)
    path.chmod(0o755)
    return path


def test_the_modem_runs_with_its_exit_code_its_own_messages_passed_on_and_its_frames_counted_and_not_logged(tmp_path):
    out = tmp_path / "modem.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "modem", "--out", str(out), "--", str(fake(tmp_path))],
        env={**os.environ, "FIXTURE": str(FIXTURES / "direwolf_stdout.txt")}, capture_output=True, timeout=20,
    )
    assert result.returncode == 3  # its exit code is the modem's, so the container's restart policy sees what really happened
    assert result.stdout.startswith(DIREWOLF[:200])  # its own messages are passed on as they are
    assert result.stdout.rstrip().endswith(b"frame heard (text and callsigns are not logged)")  # the last frame is named, not shown
    assert b"last frame" not in result.stdout and b"N9XYZ" not in result.stdout
    counters = json.loads(out.read_text())["counters"]
    assert counters["frames_heard"] == 4 and counters["frames_sent"] == 1
    assert b"N9XYZ" not in out.read_bytes()


def test_the_modem_still_runs_when_there_is_nowhere_to_write(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "modem", "--out", "/nonexistent-folder-for-test/modem.json", "--", str(fake(tmp_path))],
        env={**os.environ, "FIXTURE": str(FIXTURES / "direwolf_stdout.txt")}, capture_output=True, timeout=20,
    )
    assert result.returncode == 3 and DIREWOLF[:100] in result.stdout


def test_a_modem_that_cannot_be_started_exits_with_a_plain_error(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), "modem", "--", str(tmp_path / "no-such-modem")], capture_output=True, timeout=20)
    assert result.returncode == 127 and b"no-such-modem" in result.stderr


def test_a_stop_signal_reaches_the_modem_and_the_reporter_exits(tmp_path):
    body = "#!/bin/sh\ntrap 'echo got-term; exit 0' TERM\necho started\nwhile :; do sleep 0.05; done\n"
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT), "modem", "--", str(fake(tmp_path, body))], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert proc.stdout.readline().strip() == b"started"
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=10)
    assert proc.returncode == 0 and b"got-term" in out


def test_a_very_long_line_is_passed_on_without_being_held_whole(tmp_path):
    body = "#!/bin/sh\nhead -c 3000000 /dev/zero | tr '\\0' 'a'\necho\necho '[0.3] A1B>W1AW-10:after'\n"
    started = time.monotonic()
    result = subprocess.run([sys.executable, str(SCRIPT), "modem", "--", str(fake(tmp_path, body))], capture_output=True, timeout=30)
    assert result.returncode == 0 and len(result.stdout) >= 3_000_000 and result.stdout.rstrip().endswith(b"(text and callsigns are not logged)")
    assert b"after" not in result.stdout  # the frame line after it is named, not shown
    assert time.monotonic() - started < 20


# -- keeping a frame's text out of the container's log ---------------------------------------------------------------------


def _filtered(lines):
    frames = rep.FrameFilter()
    out = []
    for line in lines:
        out += frames.feed(line)
    return b"".join(out).decode()


def test_a_heard_frames_block_becomes_one_line_with_the_audio_level_and_no_call_or_text():
    text = _filtered(DIREWOLF.splitlines(keepends=True))
    for secret in ("N0CALL-1", "K1ABC", "quick brown fox", "hello from a test", "test beacon", "Position, House QTH", "N 42 37.1400"):
        assert secret not in text
    assert text.count("frame heard, audio level 80(23/22)") == 1 and text.count("frame heard") == 3
    assert text.count("frame sent") == 1 and "W1AW-10>CQ" not in text


def test_everything_that_is_not_about_a_frame_passes_on_unchanged():
    lines = [line for line in DIREWOLF.splitlines(keepends=True)]
    text = _filtered(lines)
    for kept in ("Dire Wolf Release 1.8.1", "Channel 0: 1200 baud", "Ready to accept KISS TCP client application 0 on port 8001",
                 "KISS client application 0 on TCP port 8001 has gone away."):
        assert kept in text
    assert _filtered([b"Could not open the sound card\n", b"\n", b"ERROR something broke\n"]) == "Could not open the sound card\n\nERROR something broke\n"


def test_a_frame_with_no_audio_level_line_is_dropped_too_and_the_block_that_never_ends_does_not_swallow_the_log():
    assert _filtered([b"[0.3] K1ABC>W1AW-10:secret words<0x0a>\n", b"decoded secret words\n", b"\n"]) == "frame heard (text and callsigns are not logged)\n\n"
    long_block = [b"[0.3] K1ABC>W1AW-10:x\n"] + [b"secret line %d\n" % n for n in range(rep.MAX_FRAME_BLOCK_LINES + 5)] + [b"after\n"]
    text = _filtered(long_block)
    assert text.endswith("after\n")
    assert "secret line 0\n" not in text and "secret line 20\n" not in text  # the block's lines are dropped up to the limit


def test_two_frames_in_a_row_with_no_blank_line_between_show_no_text():
    text = _filtered([b"K1ABC audio level = 70(20/20)   __\n", b"[0.3] K1ABC>W1AW-10:one\n", b"[0.3] N0CALL>W1AW-10:two\n", b"\n"])
    assert "frame heard" in text and "one" not in text and "two" not in text and "K1ABC" not in text and "N0CALL" not in text
