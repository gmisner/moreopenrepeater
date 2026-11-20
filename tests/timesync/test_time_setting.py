"""The NTP server setting: what may be stored (it ends up in chrony's configuration) and how it is stored."""
import json
import os
import stat

import pytest

from api import time_sync as ts

GOOD = [
    "pool.ntp.org", "0.pool.ntp.org", "time.cloudflare.com", "ntp-1.example.net", "A.B.C", "gps-clock", "a", "x" * 63,
    "192.168.1.10", "10.0.0.1", "8.8.8.8", "2001:db8::1", "2606:4700:f1::1", "::ffff:192.0.2.1",
    ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 57]),  # 253 characters
    "xn--bcher-kva.example",  # an internationalized name in its ASCII form
]

BAD = [
    ("-pool.ntp.org", "leading dash"), ("-", "just a dash"), ("--version", "an option"), ("-x", "an option"), ("pool.ntp.org -x", "space then option"),
    (" pool.ntp.org", "leading space"), ("pool.ntp.org ", "trailing space"), ("pool .ntp.org", "inner space"), ("pool\tntp.org", "tab"),
    ("pool.ntp.org\n", "trailing newline"), ("pool.ntp.org\nserver evil.example", "a second directive"), ("a\rb", "carriage return"),
    ("a\x00b", "NUL"), ("a\x1bb", "escape"), ("pool.ntp.org x", "line separator"),
    ("a;b", "semicolon"), ("a&b", "ampersand"), ("a|b", "pipe"), ("a$b", "dollar"), ("$(id)", "command substitution"), ("`id`", "backticks"),
    ("a'b", "quote"), ('a"b', "double quote"), ("a\\b", "backslash"), ("a#b", "comment"), ("a<b", "less than"), ("a>b", "greater than"),
    ("a(b)", "parentheses"), ("a{b}", "braces"), ("a[b]", "brackets"), ("a*b", "star"), ("a?b", "question mark"), ("a=b", "equals"),
    ("a/b", "slash"), ("a%b", "percent"), ("a@b", "at"), ("a,b", "comma"), ("a!b", "bang"), ("a~b", "tilde"), ("a+b", "plus"), ("a_b.example", "underscore"),
    ("fe80::1%eth0", "IPv6 zone"), ("1.2.3.4/24", "a network"), ("1.2.3.4:123", "address and port"), ("[::1]", "bracketed"), ("http://a.example", "a URL"),
    ("pool.ntp.org:123", "name and port"),
    ("bücher.example", "unicode letter"), ("ntp。example", "ideographic full stop"), ("ｐool.example", "fullwidth letter"), ("日本.example", "CJK"),
    ("pool​.example", "zero width space"), ("á.example", "combining mark"),
    ("pool..ntp.org", "empty label"), (".pool.ntp.org", "leading dot"), ("pool.ntp.org.", "trailing dot"), (".", "dot"), ("..", "dots"),
    ("a-.example", "label ends with hyphen"), ("-a.example", "label starts with hyphen"), ("a.-b.example", "inner label starts with hyphen"),
    ("x" * 64, "label of 64"), ("a." + "x" * 64, "second label of 64"),
    ("a" * 254, "254 characters"), (".".join(["a" * 63] * 4 + ["bb"]), "over 253 in labels"),
    ("1.2.3", "three numbers"), ("1.2.3.4.5", "five numbers"), ("999.1.1.1", "not an address"), ("256.0.0.1", "octet too big"), ("1.2.3.04", "leading zero"),
    ("example.123", "name ending in a number"), ("0x7f.0.0.1", "hex octet"), ("1e3.example.1", "name ending in a number"),
    ("0.0.0.0", "unspecified"), ("127.0.0.1", "loopback"), ("127.1.2.3", "loopback range"), ("224.0.0.1", "multicast"), ("255.255.255.255", "broadcast"),
    ("240.0.0.1", "reserved"), ("::", "IPv6 unspecified"), ("::1", "IPv6 loopback"), ("ff02::1", "IPv6 multicast"), ("1:2:3", "not an address"), ("::g", "bad hex"),
    ("localhost", "this machine"), ("LOCALHOST", "this machine, upper case"),
]


@pytest.mark.parametrize("value", GOOD)
def test_a_host_name_or_ip_address_is_accepted_unchanged(value):
    assert ts.check_server(value) == value


def test_empty_means_the_default():
    assert ts.check_server("") == ""


@pytest.mark.parametrize("value, why", BAD, ids=[why for _v, why in BAD])
def test_everything_else_is_refused(value, why):
    with pytest.raises(ts.TimeServerError):
        ts.check_server(value)


@pytest.mark.parametrize("value", [None, 123, 1.5, True, b"pool.ntp.org", ["pool.ntp.org"], {"a": 1}])
def test_a_value_that_is_not_text_is_refused(value):
    with pytest.raises(ts.TimeServerError):
        ts.check_server(value)


def test_the_error_is_a_plain_sentence():
    with pytest.raises(ts.TimeServerError) as caught:
        ts.check_server("a b")
    assert "letters, digits, dots and hyphens" in str(caught.value) and "—" not in str(caught.value)


def test_nothing_that_passes_could_add_a_line_or_a_directive_to_a_configuration_file():
    for value in GOOD:
        assert all(char.isalnum() or char in ".-:" for char in ts.check_server(value)) and not value.startswith("-")


# -- the file --------------------------------------------------------------------------------------------------------------------


def test_the_setting_is_a_small_json_file_everyone_can_read(tmp_path):
    setting = ts.NtpSetting(tmp_path / "time-config")
    setting.set("time.example.org")
    path = tmp_path / "time-config" / "ntp.json"
    assert json.loads(path.read_text()) == {"server": "time.example.org"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o644  # the time container is another user and only reads it
    assert setting.get() == "time.example.org" and setting.effective == "time.example.org"


def test_empty_means_the_default_server_is_checked(tmp_path):
    setting = ts.NtpSetting(tmp_path)
    assert setting.get() == "" and setting.effective == ts.DEFAULT_SERVER == "pool.ntp.org"
    setting.set("time.example.org")
    setting.set("")
    assert setting.get() == "" and setting.effective == "pool.ntp.org"
    assert json.loads((tmp_path / "ntp.json").read_text()) == {"server": ""}


def test_a_bad_value_is_refused_before_anything_is_written(tmp_path):
    tmp_path = tmp_path / "cfg"  # the autouse log file is in tmp_path itself
    setting = ts.NtpSetting(tmp_path)
    setting.set("good.example")
    with pytest.raises(ts.TimeServerError):
        setting.set("bad value; rm -rf /")
    assert setting.get() == "good.example"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ntp.json"]


def test_the_write_is_atomic_a_failure_midway_leaves_the_old_file_and_no_litter(tmp_path, monkeypatch):
    tmp_path = tmp_path / "cfg"
    setting = ts.NtpSetting(tmp_path)
    setting.set("old.example")
    real_replace = os.replace

    def fail(src, dst):
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        setting.set("new.example")
    monkeypatch.setattr(os, "replace", real_replace)
    assert json.loads((tmp_path / "ntp.json").read_text()) == {"server": "old.example"}
    assert [p.name for p in tmp_path.iterdir()] == ["ntp.json"]  # the temp file was cleaned up


def test_a_reader_never_sees_half_a_file(tmp_path, monkeypatch):
    """The new content is complete and flushed before it is renamed over the old, and it is written beside it, not over it."""
    setting = ts.NtpSetting(tmp_path)
    setting.set("old.example")
    seen = []
    real_replace = os.replace

    def look(src, dst):
        seen.append((json.loads(open(dst).read()), json.loads(open(src).read())))  # at the moment of the swap: old is whole, new is whole
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", look)
    setting.set("new.example")
    assert seen == [({"server": "old.example"}, {"server": "new.example"})]


def test_a_missing_unreadable_or_tampered_file_is_the_default_never_an_error(tmp_path):
    setting = ts.NtpSetting(tmp_path)
    assert setting.get() == ""
    for content in ("not json", "[]", '{"server": 5}', '{"server": "a b"}', '{"server": "x\\ny"}', '{"other": 1}', "", "x" * 10000, '{"server": "-evil"}'):
        (tmp_path / "ntp.json").write_text(content)
        assert setting.get() == "", content
        assert setting.effective == "pool.ntp.org"


def test_with_no_folder_the_setting_is_kept_in_memory():
    setting = ts.NtpSetting(None)
    setting.set("memory.example")
    assert setting.get() == "memory.example"


def test_the_new_file_is_flushed_to_disk_before_it_is_renamed_over_the_old(tmp_path, monkeypatch):
    """A power cut after the rename must not leave an empty file: the data is synced first, then the swap."""
    order = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(os, "replace", lambda a, b: (order.append("replace"), real_replace(a, b))[1])
    ts.NtpSetting(tmp_path / "cfg").set("time.example.org")
    assert order[:2] == ["fsync", "replace"]
