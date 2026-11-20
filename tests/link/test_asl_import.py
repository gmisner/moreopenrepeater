"""Reading an existing node's files (link.asl_import) and the two writers the importer adds (link.asl_config).

The fixtures are real files in tests/link/fixtures/asl_import: an ASL3-style default with templates, a SimpleUSB node, an
included file and registration lines, and a messy one. Node numbers and passwords in them are made up."""
import random
import shutil
from pathlib import Path

import pytest

from link import asl_import
from link.asl_config import (
    add_section_entries,
    nodes,
    release_node,
    set_section_setting,
)
from link.asl_import import read_conf, read_password, scan

FIXTURES = Path(__file__).parent / "fixtures" / "asl_import"
PASSWORD = "FAKE-pass-123"


def folder(tmp_path, name="asl3_default"):
    target = tmp_path / "src"
    shutil.copytree(FIXTURES / name, target)
    return target


def node(result, number):
    return next(n for n in result.nodes if n.number == number)


# -- an ASL3-style default ---------------------------------------------------------------------------------------------


def test_the_asl3_default_gives_the_node_its_callsign_from_the_template(tmp_path):
    result = scan(folder(tmp_path))
    assert result.found and not result.problems
    n = node(result, "99001")
    assert n.callsign == "N0CALL"  # idrecording is in [node-main], not in the node's own stanza
    assert n.startup_macro == "*81"
    assert n.radio == "SimpleUSB/usb_fixture"


def test_the_links_are_the_nodes_list_without_itself_or_the_loopback(tmp_path):
    n = node(scan(folder(tmp_path)), "99001")
    assert n.links == {"99002": "radio@203.0.113.7:4569/99002,NONE", "99003": "radio@203.0.113.8/99003,NONE"}
    assert n.links_skipped == 1  # 99004 is on 127.0.0.1: in the container that would be the container itself


def test_a_node_in_an_included_file_is_found_and_the_include_is_reported(tmp_path):
    result = scan(folder(tmp_path))
    assert node(result, "99006").radio == "Local/pseudo"
    assert next(f for f in result.files if f.name == "rpt.conf").included == ["custom/rpt/extra.conf"]


def test_the_node_extensions_names_is_the_suggested_one(tmp_path):
    src = folder(tmp_path)
    assert [n.number for n in scan(src).nodes if n.suggested] == ["99001"]
    # even when another node is the one that registers, extensions.conf's NODE wins
    (src / "extensions.conf").write_text("[globals]\nNODE = 99006\n")
    assert [n.number for n in scan(src).nodes if n.suggested] == ["99006"]
    # with no hint, the node that registers is the one
    (src / "extensions.conf").unlink()
    assert [n.number for n in scan(src).nodes if n.suggested] == ["99001"]


def test_the_links_list_a_template_names_is_the_one_read(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "rpt.conf").write_text(
        "[node-main](!)\nnodes = our-links\n\n[99001](node-main)\n\n"
        "[nodes]\n99010 = radio@203.0.113.50/99010,NONE\n\n[our-links]\n99011 = radio@203.0.113.51/99011,NONE\n"
    )
    assert node(scan(src), "99001").links == {"99011": "radio@203.0.113.51/99011,NONE"}


def test_registration_is_found_in_either_file_and_only_returned_by_the_password_function(tmp_path):
    src = folder(tmp_path)
    assert node(scan(src), "99001").has_registration
    assert read_password(src, "99001") == PASSWORD
    (src / "rpt_http_registrations.conf").unlink()
    assert read_password(src, "99001") == PASSWORD  # iax.conf's line, as ASL2 kept it
    (src / "iax.conf").write_text("[general]\n;register => 99001:commented-out-pw@register.allstarlink.org\n")
    assert read_password(src, "99001") is None and not node(scan(src), "99001").has_registration  # a commented line isn't one


def test_savenode_conf_is_a_last_resort_for_the_password(tmp_path):
    src = folder(tmp_path)
    for name in ("rpt_http_registrations.conf", "iax.conf"):
        (src / name).unlink()
    (src / "savenode.conf").write_text("NODE=99001\nPASSWORD=Savenode-pw-1\nENABLE=1\n")
    assert read_password(src, "99001") == "Savenode-pw-1"
    (src / "savenode.conf").write_text("NODE=99002\nPASSWORD=Other-nodes-pw\n")
    assert read_password(src, "99001") is None  # another node's


def test_nothing_the_scan_returns_holds_a_password_or_another_files_secret(tmp_path):
    result = scan(folder(tmp_path))
    assert PASSWORD not in repr(result)
    for secret in ("iax-user-secret-fixture", "manager-secret-fixture"):
        assert secret not in repr(result)


def test_the_radio_files_and_the_management_login_are_never_opened(tmp_path, monkeypatch):
    opened = []
    real = asl_import._read_text
    monkeypatch.setattr(asl_import, "_read_text", lambda path: (opened.append(path.name), real(path))[1])
    scan(folder(tmp_path))
    assert not {"simpleusb.conf", "usbradio.conf", "voter.conf", "manager.conf"} & set(opened)
    assert "rpt.conf" in opened


# -- messy files -------------------------------------------------------------------------------------------------------


def test_a_messy_file_gives_what_it_can_and_never_raises(tmp_path):
    result = scan(folder(tmp_path, "messy"))
    numbers = [n.number for n in result.nodes]
    assert numbers == ["77001", "77002"]  # not 88888 (in a block comment), not the 13-digit one, the repeated one once
    a = node(result, "77001")
    assert a.callsign == "N0CALL/P"  # CRLF line endings, tabs and padding around the setting
    assert a.radio == "Radio/usb"  # two templates: the last one listed wins, as in Asterisk
    assert "isn't a plain DTMF string" in " ".join(a.notes) and a.startup_macro == ""
    b = node(result, "77002")
    assert b.callsign == "" and "recording" in " ".join(b.notes)
    assert b.startup_macro == ""  # `rm -rf /` is not a macro
    assert a.links == {"77002": "radio@203.0.113.20/77002,NONE"} and a.links_skipped == 1


def test_exec_is_never_run_and_includes_cannot_leave_the_folder(tmp_path):
    result = scan(folder(tmp_path, "messy"))
    detail = next(f for f in result.files if f.name == "rpt.conf").detail
    assert "#exec line was not run" in detail and "outside the folder" in detail and "does-not-exist.conf isn't there" in detail


def test_an_include_through_a_symlink_out_of_the_folder_is_not_read(tmp_path):
    src = folder(tmp_path)
    outside = tmp_path / "outside.conf"
    outside.write_text("[99777](node-main)\nrxchannel = Local/x\n")
    (src / "custom" / "rpt" / "link.conf").symlink_to(outside)
    result = scan(src)
    assert "99777" not in [n.number for n in result.nodes]


def test_include_loops_and_deep_nesting_stop(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "rpt.conf").write_text('[99001]\n#include "a.conf"\n')
    (src / "a.conf").write_text('#include "b.conf"\nrxchannel = Local/a\n')
    (src / "b.conf").write_text('#include "a.conf"\n#include "rpt.conf"\nidrecording = |iN0CALL\n')
    result = scan(src)
    assert node(result, "99001").callsign == "N0CALL"  # a, b and rpt.conf each read once


def test_an_unterminated_block_comment_ends_the_file_not_the_dashboard(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "rpt.conf").write_text("[99001]\nidrecording = |iN0CALL\n;-- never closed\n[99002]\n")
    assert [n.number for n in scan(src).nodes] == ["99001"]


def test_bytes_that_are_not_text_are_replaced_not_fatal(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "rpt.conf").write_bytes(b"[99001]\nidrecording = |iN0CALL\n\xff\xfe\x80 garbage\n")
    assert node(scan(src), "99001").callsign == "N0CALL"


def test_a_huge_file_is_refused_with_the_reason(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "rpt.conf").write_text("[99001]\n" + "x = y\n" * 250_000)
    result = scan(src)
    assert not result.found and any("couldn't be read" in p and "KB" in p for p in result.problems)


def test_random_garbage_never_raises(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    rng = random.Random(7)
    alphabet = list("[]()=;#,!+\"'<>\\/\n\r\t -:@|*0123456789abcdefNODEinclude") + ["#include ", "#exec ", ";--", "--;", "[1999]", "(node-main)", "register =>"]
    for _ in range(300):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 300)))
        (src / "rpt.conf").write_text(text)
        (src / "iax.conf").write_text(text[::-1])
        result = scan(src)
        assert isinstance(result.problems, list)
        read_password(src, "1999")


# -- missing and unreadable --------------------------------------------------------------------------------------------


def test_an_empty_folder_says_rpt_conf_isnt_there(tmp_path):
    (tmp_path / "empty").mkdir()
    result = scan(tmp_path / "empty")
    assert not result.found and result.problems == [f"rpt.conf isn't in {tmp_path / 'empty'}."]
    assert [(f.name, f.status) for f in result.files] == [("rpt.conf", "missing")]


def test_a_folder_that_isnt_one_is_said_so(tmp_path):
    assert scan(tmp_path / "nope").problems == [f"{tmp_path / 'nope'} isn't a folder."]
    (tmp_path / "file").write_text("x")
    assert "isn't a folder" in scan(tmp_path / "file").problems[0]


def test_a_file_that_cant_be_read_is_named_with_the_reason(tmp_path, monkeypatch):
    src = folder(tmp_path)
    real = asl_import._read_text

    def deny(path):
        if path.name == "iax.conf":
            raise PermissionError(13, "Permission denied")
        return real(path)

    monkeypatch.setattr(asl_import, "_read_text", deny)
    result = scan(src)
    iax = next(f for f in result.files if f.name == "iax.conf")
    assert iax.status == "unreadable" and "permission denied" in iax.detail and "user 999" in iax.detail
    assert result.found  # the rest still previews


def test_a_file_with_no_permissions_is_unreadable_for_real(tmp_path):
    import os

    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    src = folder(tmp_path)
    (src / "rpt_http_registrations.conf").chmod(0)
    result = scan(src)
    assert next(f for f in result.files if f.name == "rpt_http_registrations.conf").status == "unreadable"
    (src / "rpt_http_registrations.conf").chmod(0o644)


def test_a_file_with_no_nodes_says_what_a_node_is(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "rpt.conf").write_text("[general]\n[node-main](!)\nduplex = 1\n")
    result = scan(src)
    assert not result.found and "no node" in result.problems[0]


def test_an_absolute_etc_asterisk_include_means_the_same_place_in_the_folder(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "extra.conf").write_text("[99001]\nidrecording = |iN0CALL\n")
    (src / "rpt.conf").write_text('#include "/etc/asterisk/extra.conf"\n')
    assert node(scan(src), "99001").callsign == "N0CALL"


def test_read_conf_cuts_block_comments_and_keeps_the_rest(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.conf").write_text("one\n;-- two\nthree --;\nfour\n")
    text, report = read_conf(src, "a.conf")
    assert text.split() == ["one", "four"] and report.status == "read"


# -- the two writers ---------------------------------------------------------------------------------------------------

RPT = """\
[node-main](!)
startup_macro = *99

[99001](node-main)
rxchannel = Local/pseudo
startup_macro = *82   ; the old one

[nodes]
99002 = radio@198.51.100.9/99002,NONE

#tryinclude "x/*.conf"
"""


def test_set_section_setting_replaces_keeps_the_old_line_as_a_comment_and_is_idempotent():
    once = set_section_setting(RPT, "99001", "startup_macro", "*81")
    assert "startup_macro = *81  ; imported by moreopenrepeater" in once
    assert ";moreopenrepeater import replaced: startup_macro = *82   ; the old one" in once
    assert once.count("startup_macro = *99") == 1  # the template's line is untouched
    assert set_section_setting(once, "99001", "startup_macro", "*81") == once


def test_set_section_setting_adds_when_the_section_has_none():
    text = set_section_setting("[99001]\nduplex = 1\n", "99001", "startup_macro", "*81")
    assert text == "[99001]\nstartup_macro = *81  ; imported by moreopenrepeater\nduplex = 1\n"


def test_an_imported_line_survives_releasing_the_node():
    """`release_node` takes out only the controller's own lines (set by moreopenrepeater), not these."""
    from link.asl_config import control_node, controller_settings

    imported = set_section_setting(RPT, "99001", "startup_macro", "*81")
    controlled = control_node(imported, "99001", controller_settings("USRP/127.0.0.1:34001:32001"))
    released = release_node(controlled, "99001")
    assert released == imported
    assert [n.number for n in nodes(released)] == ["99001"]


def test_add_section_entries_adds_only_what_is_missing_and_never_overwrites():
    text = add_section_entries(RPT, "nodes", {"99002": "radio@203.0.113.7/99002,NONE", "99003": "radio@203.0.113.8/99003,NONE"})
    assert "99002 = radio@198.51.100.9/99002,NONE" in text and "203.0.113.7" not in text
    assert "99003 = radio@203.0.113.8/99003,NONE  ; imported by moreopenrepeater" in text
    assert text.index("99003 =") < text.index("#tryinclude")  # inside the stanza, before the include
    assert add_section_entries(text, "nodes", {"99003": "radio@203.0.113.8/99003,NONE"}) == text


def test_add_section_entries_creates_the_stanza_when_it_is_missing():
    text = add_section_entries("[99001]\nduplex = 1\n", "nodes", {"99002": "radio@203.0.113.7/99002,NONE"})
    assert text.endswith("\n[nodes]\n99002 = radio@203.0.113.7/99002,NONE  ; imported by moreopenrepeater\n")
