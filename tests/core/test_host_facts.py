"""The core's reader of the host's kernel log (`/dev/kmsg`), against fake sources: a real file or FIFO with realistic records."""
import errno
import os
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core import host_facts
from core.host_facts import NOT_ENABLED, read_kernel_log

# What /dev/kmsg gives: priority,sequence,microseconds,flags;message, then continuation lines that start with a space.
AIOC = b"6,31797,143820904747,-;usb 1-1.2: Manufacturer: AIOC\n SUBSYSTEM=usb\n DEVICE=c189:5\n"
EMI = b"4,31900,143999000001,-;usb 1-1-port2: disabled by hub (EMI?)\n SUBSYSTEM=usb\n"
CPU = b"6,31901,144000000000,-;cpu cpu0: dummy message\n"


def source(tmp_path, *chunks: bytes, name="kmsg") -> Path:
    path = tmp_path / name
    path.write_bytes(b"".join(chunks))
    return path


def records(n, start=1):
    return b"".join(b"6,%d,%d,-;message number %d\n SUBSYSTEM=test\n" % (i, i * 1000, i) for i in range(start, start + n))


def test_records_come_back_oldest_first_as_a_timestamp_and_a_message(tmp_path):
    lines, error = read_kernel_log(source(tmp_path, AIOC, EMI, CPU))
    assert error is None
    assert lines == [
        "[143820.904747] usb 1-1.2: Manufacturer: AIOC",
        "[143999.000001] usb 1-1-port2: disabled by hub (EMI?)",
        "[144000.000000] cpu cpu0: dummy message",
    ]


def test_continuation_lines_that_start_with_a_space_are_not_lines_of_their_own(tmp_path):
    lines, _ = read_kernel_log(source(tmp_path, AIOC))
    assert lines == ["[143820.904747] usb 1-1.2: Manufacturer: AIOC"]  # SUBSYSTEM= and DEVICE= are about the record


def test_the_wall_clock_time_is_put_in_front_when_the_boot_time_is_known(tmp_path):
    boot = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    lines, _ = read_kernel_log(source(tmp_path, b"6,1,3723500000,-;hello\n"), btime=boot)
    assert lines == ["2026-11-19 13:02:03Z [ 3723.500000] hello"]


@pytest.mark.parametrize("bad", [
    b"no separator at all\n",
    b"6,1;missing the timestamp field\n",
    b"6,1,not-a-number,-;a timestamp that is not\n",
    b"6,1,-5,-;negative\n",
    b"\n",
])
def test_a_record_that_is_not_one_is_skipped_and_the_rest_are_read(tmp_path, bad):
    lines, error = read_kernel_log(source(tmp_path, AIOC, bad, EMI))
    assert error is None and [line.split("] ", 1)[1] for line in lines] == ["usb 1-1.2: Manufacturer: AIOC", "usb 1-1-port2: disabled by hub (EMI?)"]


def test_bytes_that_are_not_text_do_not_stop_it(tmp_path):
    lines, error = read_kernel_log(source(tmp_path, b"6,1,1000000,-;bad \xff\xfe byte\n"))
    assert error is None and "bad" in lines[0] and "byte" in lines[0]


def test_an_empty_source_is_an_empty_log_not_an_error(tmp_path):
    assert read_kernel_log(source(tmp_path)) == ([], None)


def test_only_the_newest_lines_are_kept(tmp_path):
    lines, _ = read_kernel_log(source(tmp_path, records(1000)), lines=400)
    assert len(lines) == 400
    assert lines[0].endswith("message number 601") and lines[-1].endswith("message number 1000")


def test_the_default_is_the_last_400(tmp_path):
    lines, _ = read_kernel_log(source(tmp_path, records(500)))
    assert len(lines) == host_facts.KERNEL_LINES == 400


def test_a_line_is_cut_at_a_limit(tmp_path):
    lines, _ = read_kernel_log(source(tmp_path, b"6,1,1000,-;" + b"x" * 5000 + b"\n"))
    assert len(lines[0]) == host_facts.MAX_LINE_CHARS


def test_a_source_far_bigger_than_the_limits_is_cut_at_the_record_limit_and_stays_small(tmp_path):
    big = source(tmp_path, records(5000))
    lines, error = read_kernel_log(big, lines=10, max_records=100)
    assert error is None and len(lines) == 10
    assert lines[-1].endswith("message number 100")  # it stopped there; the other 4900 were not read


def test_it_stops_reading_at_the_record_limit_and_does_not_go_on_to_the_end(tmp_path, monkeypatch):
    reads = []
    real = host_facts._read_once
    monkeypatch.setattr(host_facts, "_read_once", lambda fd: reads.append(1) or real(fd))
    read_kernel_log(source(tmp_path, records(5000)), lines=10, max_records=100)
    assert len(reads) == 1  # one 8 KB read held more than 100 records


def test_a_source_far_bigger_than_the_byte_limit_is_not_read_past_it(tmp_path):
    big = source(tmp_path, records(5000))
    lines, error = read_kernel_log(big, lines=5000, max_bytes=host_facts.READ_SIZE * 3)
    assert error is None and 0 < len(lines) < 5000
    assert len(lines) * 45 < host_facts.READ_SIZE * 4  # about three reads' worth of records, with the one that was in flight


def test_a_source_that_never_ends_is_cut_at_the_time_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(host_facts, "_read_once", lambda fd: b"6,1,1000,-;forever\n")
    ticks = iter(range(1000))
    lines, error = read_kernel_log(
        source(tmp_path), lines=5, max_records=10**6, max_bytes=10**12, max_seconds=3, clock=lambda: next(ticks) * 1.0
    )
    assert error is None and len(lines) == 2  # the clock said time was up after two reads, not a million


def test_a_record_split_across_reads_is_put_back_together(tmp_path, monkeypatch):
    chunks = iter([b"6,1,1000000,-;first ", b"half and second half\n6,2,2000000,-;next\n SUBSYS", b"TEM=x\n", b""])
    monkeypatch.setattr(host_facts, "_read_once", lambda fd: next(chunks))
    lines, _ = read_kernel_log(source(tmp_path))
    assert lines == ["[    1.000000] first half and second half", "[    2.000000] next"]


def test_the_last_record_without_a_newline_is_still_a_record(tmp_path):
    lines, _ = read_kernel_log(source(tmp_path, b"6,1,1000000,-;no newline at the end"))
    assert lines == ["[    1.000000] no newline at the end"]


def test_eagain_is_the_end_of_the_log_not_an_error(tmp_path, monkeypatch):
    sent = iter([AIOC, EMI])

    def read(fd):
        try:
            return next(sent)
        except StopIteration:
            raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable") from None

    monkeypatch.setattr(host_facts, "_read_once", read)
    lines, error = read_kernel_log(source(tmp_path))
    assert error is None and len(lines) == 2


def test_epipe_means_records_were_overwritten_and_it_reads_on(tmp_path, monkeypatch):
    script = iter([AIOC, OSError(errno.EPIPE, "Broken pipe"), EMI, b""])

    def read(fd):
        item = next(script)
        if isinstance(item, OSError):
            raise item
        return item

    monkeypatch.setattr(host_facts, "_read_once", read)
    lines, error = read_kernel_log(source(tmp_path))
    assert error is None and [line.split("] ", 1)[1] for line in lines] == [
        "usb 1-1.2: Manufacturer: AIOC", "usb 1-1-port2: disabled by hub (EMI?)",
    ]


def test_a_source_that_says_epipe_forever_is_given_up_on(tmp_path, monkeypatch):
    calls = []

    def read(fd):
        calls.append(1)
        raise OSError(errno.EPIPE, "Broken pipe")

    monkeypatch.setattr(host_facts, "_read_once", read)
    lines, error = read_kernel_log(source(tmp_path))
    assert lines is None and "could not read" in error
    assert len(calls) == host_facts.MAX_OVERWRITES + 1


def test_a_read_error_after_some_lines_keeps_them(tmp_path, monkeypatch):
    script = iter([AIOC, OSError(errno.EIO, "Input/output error")])

    def read(fd):
        item = next(script)
        if isinstance(item, OSError):
            raise item
        return item

    monkeypatch.setattr(host_facts, "_read_once", read)
    assert read_kernel_log(source(tmp_path)) == (["[143820.904747] usb 1-1.2: Manufacturer: AIOC"], None)


def test_a_read_error_before_any_line_is_the_reason(tmp_path, monkeypatch):
    def read(fd):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(host_facts, "_read_once", read)
    assert read_kernel_log(source(tmp_path)) == (None, f"could not read {tmp_path / 'kmsg'}: Input/output error")


def test_a_fifo_with_a_writer_and_nothing_to_say_does_not_block_it(tmp_path):
    fifo = tmp_path / "kmsg"
    os.mkfifo(fifo)
    writer_fd = os.open(fifo, os.O_RDWR)  # a writer that stays open and writes nothing: a blocking read would hang
    try:
        done = []
        thread = threading.Thread(target=lambda: done.append(read_kernel_log(fifo)), daemon=True)
        thread.start()
        thread.join(5)
        assert done == [([], None)]
    finally:
        os.close(writer_fd)


def test_a_fifo_with_records_in_it_is_read_to_the_end(tmp_path):
    fifo = tmp_path / "kmsg"
    os.mkfifo(fifo)
    writer_fd = os.open(fifo, os.O_RDWR)
    try:
        os.write(writer_fd, AIOC + EMI)
        lines, error = read_kernel_log(fifo)
        assert error is None and len(lines) == 2
    finally:
        os.close(writer_fd)


def test_the_null_device_is_not_enabled_rather_than_empty():
    assert read_kernel_log(Path("/dev/null")) == (None, NOT_ENABLED)
    assert "MOR_KMSG_DEVICE=/dev/kmsg" in NOT_ENABLED


def test_the_null_device_is_known_by_its_number_on_linux():
    null = os.stat_result((stat.S_IFCHR | 0o666, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    fake = type("Info", (), {"st_mode": stat.S_IFCHR | 0o666, "st_rdev": os.makedev(1, 3)})
    assert host_facts._is_null_device(fake) and not null.st_rdev
    other_char = type("Info", (), {"st_mode": stat.S_IFCHR | 0o666, "st_rdev": os.makedev(1, 11)})  # /dev/kmsg itself is 1,11
    assert not host_facts._is_null_device(other_char)
    plain_file = type("Info", (), {"st_mode": stat.S_IFREG | 0o644, "st_rdev": os.makedev(1, 3)})
    assert not host_facts._is_null_device(plain_file)  # only a character device counts


def test_an_empty_plain_file_is_not_mistaken_for_the_null_device(tmp_path):
    assert read_kernel_log(source(tmp_path)) == ([], None)


def test_a_missing_path_says_it_is_not_there(tmp_path):
    lines, error = read_kernel_log(tmp_path / "nope")
    assert lines is None and "is not there" in error and "was not given" in error


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read anything")
def test_permission_denied_says_so_and_names_the_likely_cause(tmp_path):
    path = source(tmp_path, AIOC)
    path.chmod(0)
    lines, error = read_kernel_log(path)
    assert lines is None and "permission denied" in error and "dmesg_restrict" in error


def test_a_directory_is_unreadable_not_a_crash(tmp_path):
    lines, error = read_kernel_log(tmp_path)
    assert lines is None and error


def test_it_does_not_leave_the_file_open(tmp_path):
    before = len(os.listdir("/dev/fd")) if os.path.isdir("/dev/fd") else None
    for _ in range(20):
        read_kernel_log(source(tmp_path, AIOC))
        read_kernel_log(Path("/dev/null"))
    if before is not None:
        assert len(os.listdir("/dev/fd")) <= before + 1


def test_the_boot_time_is_read_from_proc_stat(tmp_path):
    stat_file = tmp_path / "stat"
    stat_file.write_text("cpu  1 2 3\nbtime 1763551200\nprocesses 99\n")
    assert host_facts.boot_time(stat_file) == 1763551200.0


@pytest.mark.parametrize("text", ["cpu 1 2 3\n", "btime nope\n", "btime\n", ""])
def test_a_proc_stat_without_a_usable_boot_time_is_none(tmp_path, text):
    stat_file = tmp_path / "stat"
    stat_file.write_text(text)
    assert host_facts.boot_time(stat_file) is None
    assert host_facts.boot_time(tmp_path / "missing") is None
