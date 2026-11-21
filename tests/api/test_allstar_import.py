"""Importing an existing AllStarLink node into the container node (api.allstar_import, docs/allstar.md).

The source is real files (tests/link/fixtures/asl_import) copied into a read-only folder, the way compose mounts it. The
container node's files are a stock ASL3 set in a temp folder. The password in the fixtures is made up; every test that
looks at a response or a log looks for it."""
import json
import logging
import os
import shutil
import stat
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.allstar_audio import AllStarAudio
from api.allstar_node import AllStarNode
from api.app import create_app
from api.assets import AudioAssetStore
from api.audit import AuditLog
from api.auth import AuthSettings
from api.autopatch import PatchSettings
from api.service import RepeaterService
from api.users import UserStore
from controller.state_machine import RepeaterConfig
from link.asterisk_files import AsteriskFiles

FIXTURES = Path(__file__).resolve().parents[1] / "link" / "fixtures" / "asl_import"
AMI = PatchSettings("127.0.0.1", 5038, "admin", "secret", "127.0.0.1", 0, "127.0.0.1:9092")
PASSWORD = "FAKE-pass-123"  # in the fixtures' registration lines
OTHER_SECRETS = ("iax-user-secret-fixture", "manager-secret-fixture")

CONTAINER_RPT = """\
[node-main](!)
rxchannel = Local/pseudo            ; No radio (hub)
duplex = 2
nodes = nodes
idrecording = |iNOTSET

[1999](node-main)
rxchannel = SimpleUSB/1999      ; SimpleUSB

[1998](node-main)

[nodes]
1999 = radio@127.0.0.1/1999,NONE
99002 = radio@198.51.100.9/99002,NONE     ; the container's own value for this one

#tryinclude "custom/rpt/*.conf"
"""
CONTAINER_MODULES = """\
[modules]
autoload = no
load = chan_simpleusb.so              ; SimpleUSB Radio Interface Channel Driver
noload = chan_usbradio.so               ; USB Console Channel Driver
noload = chan_usrp.so                   ; USRP Channel Module
"""
CONTAINER_REG = """\
[general]
register_interval = 180

[registrations]
;register => 1999:password@register.allstarlink.org
"""
CONTAINER_IAX = """\
[general]
bindport = 4569
;register => 1999:password@register.allstarlink.org
"""
CONTAINER_EXTENSIONS = "[globals]\nNODE = 1999\n"
CONTAINER_SIMPLEUSB = "[1999]\neeprom = 0\n"


class FakeAMI:
    def __init__(self):
        self.commands = []

    def client(self, *args):
        return self

    async def connect(self): pass

    async def send_action(self, fields):
        self.commands.append(fields["Command"])
        raise ConnectionError("AMI connection closed before a response arrived")

    async def close(self): pass


def source_copy(tmp_path, fixture="asl3_default"):
    """The fixture as a folder the API only reads: mounted read-only, like the compose file does."""
    folder = tmp_path / "import"
    shutil.copytree(FIXTURES / fixture, folder)
    return folder


def lock(folder):
    for path in [*folder.rglob("*"), folder]:
        path.chmod(0o555 if path.is_dir() else 0o444)


def unlock(folder):
    for path in [folder, *folder.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)


def tree(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.fixture
def site(tmp_path, monkeypatch, caplog):
    """(client, etc, source, ami, audit): the app with the container node's files and a read-only source folder."""
    caplog.set_level(logging.DEBUG)
    source = source_copy(tmp_path)
    lock(source)
    monkeypatch.setenv("MOREOPENREPEATER_ALLSTAR_IMPORT_DIR", str(source))
    monkeypatch.setenv("MOREOPENREPEATER_ALLSTAR_IMPORT_SOURCE", "/etc/asterisk")
    etc = tmp_path / "etc"
    etc.mkdir()
    for name, text in {
        "rpt.conf": CONTAINER_RPT,
        "modules.conf": CONTAINER_MODULES,
        "rpt_http_registrations.conf": CONTAINER_REG,
        "iax.conf": CONTAINER_IAX,
        "extensions.conf": CONTAINER_EXTENSIONS,
        "simpleusb.conf": CONTAINER_SIMPLEUSB,
    }.items():
        (etc / name).write_text(text)
    service = RepeaterService(config=RepeaterConfig())
    ami = FakeAMI()
    node = AllStarNode(
        AsteriskFiles(str(etc), backup_dir=tmp_path / "backups"),
        AMI,
        AllStarAudio(service, None),
        listen=("127.0.0.1", 0),
        ami_factory=ami.client,
    )
    audit = AuditLog()
    app = create_app(
        service=service,
        start_background_tick=False,
        assets_store=AudioAssetStore(Path(tempfile.mkdtemp()) / "audio"),
        log_path=tmp_path / "test.log",
        allstar_node=node,
        audit=audit,
    )
    with TestClient(app) as client:
        yield client, etc, source, ami, audit
    unlock(source)


def everything_said(tmp_path, audit, *responses):
    """All the text a password could have leaked into: responses, the app's log file, captured log records, the audit trail."""
    texts = [r.text for r in responses]
    log = tmp_path / "test.log"
    texts.append(log.read_text() if log.exists() else "")
    texts.append(" ".join(f"{e.actor} {e.action} {e.detail}" for e in audit.recent()))
    return "\n".join(texts)


def snapshot_of(etc):
    return {p.name: p.read_text() for p in sorted(etc.iterdir()) if p.is_file()}


# -- the preview ------------------------------------------------------------------------------------------------------


def test_the_preview_says_what_it_found_and_what_it_will_and_will_not_take(site, tmp_path, caplog):
    client, etc, source, _ami, audit = site
    before = snapshot_of(etc)
    response = client.get("/api/allstar/import")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] and body["available"] and body["found"] and body["source"] == "/etc/asterisk"
    by_number = {n["number"]: n for n in body["nodes"]}
    assert set(by_number) == {"99001", "99006"}  # the second one is in a file rpt.conf includes
    node = by_number["99001"]
    assert node["callsign"] == "N0CALL"  # from the template
    assert node["allowed_links"] == 2 and node["startup_macro"] == "*81" and node["has_registration"] is True
    assert node["radio"] == "SimpleUSB/usb_fixture" and node["suggested"] is True and by_number["99006"]["suggested"] is False
    assert any("Callsign" in line for line in body["will_take"]) and any("Registration password" in line for line in body["will_take"])
    assert any("Radio settings" in line for line in body["will_not_take"])
    assert any("Sound card" in line for line in body["will_not_take"])
    assert not any("rxchannel" in line or ".conf" in line for line in body["will_not_take"])  # plain words, not file names
    assert {f["name"]: f["status"] for f in body["files"]}["rpt.conf"] == "read"
    assert body["target"]["99001"] == {"state": "new", "existing": "1999", "reason": None}
    assert snapshot_of(etc) == before  # a preview writes nothing
    assert not (tmp_path / "backups").exists()
    said = everything_said(tmp_path, audit, response) + caplog.text
    assert PASSWORD not in said and not any(secret in said for secret in OTHER_SECRETS)


def test_without_a_folder_the_preview_says_nothing_was_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("MOREOPENREPEATER_ALLSTAR_IMPORT_DIR", raising=False)
    node = AllStarNode(None, AMI, AllStarAudio(RepeaterService(config=RepeaterConfig()), None), listen=("127.0.0.1", 0))
    app = create_app(
        service=RepeaterService(config=RepeaterConfig()), start_background_tick=False,
        assets_store=AudioAssetStore(Path(tempfile.mkdtemp()) / "audio"), log_path=tmp_path / "t.log", allstar_node=node,
    )
    with TestClient(app) as client:
        body = client.get("/api/allstar/import").json()
        assert body["configured"] is False and body["found"] is False and body["nodes"] == []
        assert client.post("/api/allstar/import", json={"node": "99001"}).status_code == 404


def make_app_with_import_dir(tmp_path, name):
    service = RepeaterService(config=RepeaterConfig())
    etc = tmp_path / f"etc-{name}"
    etc.mkdir()
    (etc / "rpt.conf").write_text(CONTAINER_RPT)
    node = AllStarNode(AsteriskFiles(str(etc), backup_dir=tmp_path / f"b-{name}"), AMI, AllStarAudio(service, None), listen=("127.0.0.1", 0))
    return create_app(service=service, start_background_tick=False, assets_store=AudioAssetStore(Path(tempfile.mkdtemp()) / "audio"),
                      log_path=tmp_path / f"{name}.log", allstar_node=node)


def test_the_empty_folder_compose_mounts_by_default_is_not_a_request_to_import(tmp_path, monkeypatch):
    """No source named and nothing in the folder: the page shows nothing, and an apply is refused."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("MOREOPENREPEATER_ALLSTAR_IMPORT_DIR", str(empty))
    monkeypatch.setenv("MOREOPENREPEATER_ALLSTAR_IMPORT_SOURCE", "")
    with TestClient(make_app_with_import_dir(tmp_path, "a")) as client:
        body = client.get("/api/allstar/import").json()
        assert body["configured"] is False and body["found"] is False and body["nodes"] == [] and body["problems"] == []
        assert client.post("/api/allstar/import", json={"node": "99001"}).status_code == 404


def test_a_folder_named_by_setup_that_turns_out_empty_says_so(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("MOREOPENREPEATER_ALLSTAR_IMPORT_DIR", str(empty))
    monkeypatch.setenv("MOREOPENREPEATER_ALLSTAR_IMPORT_SOURCE", "/etc/asterisk")
    with TestClient(make_app_with_import_dir(tmp_path, "b")) as client:
        body = client.get("/api/allstar/import").json()
    assert body["configured"] is True and body["found"] is False and body["nodes"] == []
    assert any("rpt.conf isn't in" in p for p in body["problems"]) and body["source"] == "/etc/asterisk"


def test_an_unreadable_rpt_conf_is_reported_with_the_reason(tmp_path, site, monkeypatch):
    client, etc, source, *_ = site
    import link.asl_import as asl_import

    real = asl_import._read_text

    def deny(path):
        if path.name == "rpt.conf":
            raise PermissionError(13, "Permission denied")
        return real(path)

    monkeypatch.setattr(asl_import, "_read_text", deny)
    body = client.get("/api/allstar/import").json()
    assert body["found"] is False
    rpt = next(f for f in body["files"] if f["name"] == "rpt.conf")
    assert rpt["status"] == "unreadable" and "permission denied" in rpt["detail"] and "999" in rpt["detail"]
    assert any("rpt.conf couldn't be read" in p for p in body["problems"])


def test_an_unreadable_registration_file_is_named_and_the_node_still_previews(site, monkeypatch):
    client, *_ = site
    import link.asl_import as asl_import

    real = asl_import._read_text
    monkeypatch.setattr(asl_import, "_read_text", lambda path: (_ for _ in ()).throw(PermissionError(13, "Permission denied")) if path.name == "rpt_http_registrations.conf" else real(path))
    body = client.get("/api/allstar/import").json()
    assert {f["name"]: f["status"] for f in body["files"]}["rpt_http_registrations.conf"] == "unreadable"
    node = next(n for n in body["nodes"] if n["number"] == "99001")
    assert node["has_registration"] is True  # iax.conf has the same line


# -- applying ---------------------------------------------------------------------------------------------------------


def test_applying_writes_the_node_into_the_container_files_and_not_the_radio(site, tmp_path):
    client, etc, source, ami, audit = site
    response = client.post("/api/allstar/import", json={"node": "99001"})
    assert response.status_code == 200, response.text
    body = response.json()
    rpt = (etc / "rpt.conf").read_text()
    assert "[99001](node-main)" in rpt and "[1999]" not in rpt
    assert "idrecording = |iN0CALL" in rpt
    assert "startup_macro = *81  ; imported by moreopenrepeater" in rpt
    # the radio is the controller's core, never the host node's
    assert "rxchannel = USRP/127.0.0.1:0:32001  ; set by moreopenrepeater" in rpt
    active = [line.split(";")[0].split("=", 1)[1].strip() for line in rpt.splitlines() if line.startswith(("rxchannel", "txchannel"))]
    assert active == ["Local/pseudo", "USRP/127.0.0.1:0:32001"]  # the template's and the controller's, nothing from the host node
    assert "txchannel" not in rpt and "idtime" not in rpt and "statpost" not in rpt  # and none of its other settings
    assert "usb_fixture" not in rpt  # not even as a comment
    assert not any("usb_fixture" in text for text in snapshot_of(etc).values())  # nor in any other file
    # the allowed links: new entries added, the container's own value for one kept, the loopback ones left out
    assert "99003 = radio@203.0.113.8/99003,NONE  ; imported by moreopenrepeater" in rpt
    assert "99002 = radio@198.51.100.9/99002,NONE     ; the container's own value for this one" in rpt
    assert "203.0.113.7" not in rpt and "99004" not in rpt
    # registration: the password reached the container's files, and only those
    assert f"register => 99001:{PASSWORD}@register.allstarlink.org" in (etc / "rpt_http_registrations.conf").read_text()
    assert "NODE = 99001" in (etc / "extensions.conf").read_text()
    assert "load = chan_usrp.so  ; set by moreopenrepeater" in (etc / "modules.conf").read_text()
    assert ami.commands == ["core restart now"]
    assert body["status"]["node"] == "99001" and body["status"]["audio"]["running"]
    assert body["took"][:2] == ["Node number 99001", "Callsign N0CALL"] and "Registration password" in body["took"]
    assert any("Radio settings" in line for line in body["left"])
    assert PASSWORD not in everything_said(tmp_path, audit, response)
    assert not any(s in everything_said(tmp_path, audit, response) for s in OTHER_SECRETS)


def test_the_source_folder_is_never_written(site):
    """Mounted read-only, and the code never tries: a write to it would raise here, where it is read-only on disk too."""
    client, etc, source, *_ = site
    if os.geteuid() == 0:
        pytest.skip("root can write to a read-only folder")
    before = tree(source)
    modes = {p: stat.S_IMODE(p.stat().st_mode) for p in source.rglob("*")}
    assert client.get("/api/allstar/import").status_code == 200
    assert client.post("/api/allstar/import", json={"node": "99001"}).status_code == 200
    assert tree(source) == before
    assert {p: stat.S_IMODE(p.stat().st_mode) for p in source.rglob("*")} == modes


def test_a_copy_of_every_file_is_kept_first_and_nothing_is_lost(site, tmp_path):
    client, etc, *_ = site
    before = snapshot_of(etc)
    assert client.post("/api/allstar/import", json={"node": "99001"}).status_code == 200
    snapshots = sorted((tmp_path / "backups").glob("import-*"))
    assert len(snapshots) == 1
    saved = {p.name: p.read_text() for p in snapshots[0].iterdir()}
    assert saved == before  # the files exactly as they were: copying them back undoes the import
    # and the ordinary per-file backups are made too, with the first copy not overwritten by a second write in the same second
    rpt_backups = sorted((tmp_path / "backups").glob("rpt.conf.*"))
    assert len(rpt_backups) >= 2
    assert rpt_backups[0].read_text() == before["rpt.conf"]


def test_applying_twice_changes_nothing_the_second_time(site):
    client, etc, *_ = site
    assert client.post("/api/allstar/import", json={"node": "99001"}).status_code == 200
    first = snapshot_of(etc)
    again = client.post("/api/allstar/import", json={"node": "99001"})
    assert again.status_code == 200, again.text
    assert snapshot_of(etc) == first
    rpt = first["rpt.conf"]
    assert rpt.count("startup_macro = *81") == 1 and rpt.count("99003 = ") == 1
    assert client.get("/api/allstar/import").json()["target"]["99001"]["state"] == "same"


def test_the_original_copy_survives_more_applies(site, tmp_path):
    client, etc, *_ = site
    before = snapshot_of(etc)
    for _ in range(12):
        assert client.post("/api/allstar/import", json={"node": "99001"}).status_code == 200
    snapshots = sorted((tmp_path / "backups").glob("import-*"))
    assert len(snapshots) <= 10
    assert {p.name: p.read_text() for p in snapshots[0].iterdir()} == before


def test_a_node_that_is_already_set_up_is_not_replaced_without_confirming(site):
    client, etc, *_ = site
    assert client.post("/api/allstar/import", json={"node": "99006", "callsign": "N0CALL"}).status_code == 200
    before = snapshot_of(etc)
    response = client.post("/api/allstar/import", json={"node": "99001"})
    assert response.status_code == 409
    assert "99006" in response.json()["detail"] and "Confirm" in response.json()["detail"]
    assert snapshot_of(etc) == before  # refused: not a line changed
    preview = client.get("/api/allstar/import").json()
    assert preview["target"]["99001"] == {"state": "replace", "existing": "99006", "reason": None}
    confirmed = client.post("/api/allstar/import", json={"node": "99001", "replace": True})
    assert confirmed.status_code == 200
    rpt = (etc / "rpt.conf").read_text()
    assert "[99001](node-main)" in rpt and "[99006]" not in rpt


def test_a_registered_container_node_counts_as_set_up_even_with_a_stock_number(site):
    client, etc, *_ = site
    reg = etc / "rpt_http_registrations.conf"
    reg.write_text(CONTAINER_REG + "register => 1999:Another-pass-9@register.allstarlink.org\n")
    assert client.post("/api/allstar/import", json={"node": "99001"}).status_code == 409
    assert client.get("/api/allstar/import").json()["target"]["99001"]["state"] == "replace"


def test_with_several_nodes_the_one_asked_for_is_the_one_imported(site):
    client, etc, *_ = site
    assert len(client.get("/api/allstar/import").json()["nodes"]) == 2
    response = client.post("/api/allstar/import", json={"node": "99006", "callsign": "n0call"})
    assert response.status_code == 200, response.text
    rpt = (etc / "rpt.conf").read_text()
    assert "[99006](node-main)" in rpt and "[99001]" not in rpt
    assert "startup_macro" not in rpt  # the macro belongs to the other node
    assert response.json()["status"]["node"] == "99006"


def test_an_unknown_node_is_not_found(site):
    client, etc, *_ = site
    before = snapshot_of(etc)
    assert client.post("/api/allstar/import", json={"node": "12345"}).status_code == 404
    assert snapshot_of(etc) == before


def test_a_node_without_a_callsign_needs_one_given(site):
    client, etc, *_ = site
    response = client.post("/api/allstar/import", json={"node": "99006"})
    assert response.status_code == 200  # 99006 inherits the template's ID
    client2_source = site[2]
    # a source with no CW ID at all
    unlock(client2_source)
    rpt = (client2_source / "rpt.conf").read_text().replace("idrecording = |iN0CALL        ; CW ID, as asl-menu sets it\n", "").replace("idtalkover = |iN0CALL\n", "")
    (client2_source / "rpt.conf").write_text(rpt)
    lock(client2_source)
    # back to a fresh container
    (etc / "rpt.conf").write_text(CONTAINER_RPT)
    refused = client.post("/api/allstar/import", json={"node": "99001"})
    assert refused.status_code == 422 and "callsign" in refused.json()["detail"].lower()
    given = client.post("/api/allstar/import", json={"node": "99001", "callsign": "w1abc"})
    assert given.status_code == 200
    assert "idrecording = |iW1ABC" in (etc / "rpt.conf").read_text()


def test_a_public_node_with_no_registration_imports_without_one(site):
    client, etc, source, *_ = site
    unlock(source)
    (source / "rpt_http_registrations.conf").write_text("[general]\n")
    (source / "iax.conf").write_text("[general]\n")
    lock(source)
    preview = client.get("/api/allstar/import").json()
    node = next(n for n in preview["nodes"] if n["number"] == "99001")
    assert node["has_registration"] is False and any("won't register" in note for note in node["notes"])
    response = client.post("/api/allstar/import", json={"node": "99001"})
    assert response.status_code == 200
    assert "register =>" not in (etc / "rpt_http_registrations.conf").read_text().replace(";register", "")
    assert "No registration password" in " ".join(response.json()["took"])


def test_a_password_the_dashboard_cannot_write_safely_is_not_carried(site, tmp_path):
    client, etc, source, _ami, audit = site
    unlock(source)
    for name in ("rpt_http_registrations.conf", "iax.conf"):
        text = (source / name).read_text().replace(PASSWORD, "bad!pw")
        (source / name).write_text(text)
    lock(source)
    preview = client.get("/api/allstar/import")
    node = next(n for n in preview.json()["nodes"] if n["number"] == "99001")
    assert node["has_registration"] and node["password_usable"] is False
    response = client.post("/api/allstar/import", json={"node": "99001"})
    assert response.status_code == 200
    assert "bad!pw" not in (etc / "rpt_http_registrations.conf").read_text()
    assert "bad!pw" not in everything_said(tmp_path, audit, preview, response)


def test_no_node_in_the_container_to_turn_into_this_one_is_said_plainly(site):
    client, etc, *_ = site
    (etc / "rpt.conf").write_text("[general]\n")
    preview = client.get("/api/allstar/import").json()
    assert preview["target"]["99001"]["state"] == "blocked" and "no node" in preview["target"]["99001"]["reason"]
    response = client.post("/api/allstar/import", json={"node": "99001"})
    assert response.status_code == 422 and "no node" in response.json()["detail"]
    assert (etc / "rpt.conf").read_text() == "[general]\n"


def test_a_failed_write_is_a_502_with_the_reason_and_no_password_in_it(site, tmp_path):
    client, etc, source, _ami, audit = site
    (etc / "rpt.conf").chmod(0o444)
    if os.geteuid() == 0:
        pytest.skip("root can write to a read-only file")
    response = client.post("/api/allstar/import", json={"node": "99001"})
    assert response.status_code == 502 and "Couldn't write" in response.json()["detail"]
    assert PASSWORD not in everything_said(tmp_path, audit, response)


def test_a_call_in_progress_holds_it_off(site, monkeypatch):
    client, etc, *_ = site
    from api.autopatch import Autopatch

    monkeypatch.setattr(Autopatch, "status", lambda self: {"call": {"number": "1"}})
    before = snapshot_of(etc)
    assert client.post("/api/allstar/import", json={"node": "99001"}).status_code == 409
    assert snapshot_of(etc) == before


@pytest.mark.parametrize("body", [{"node": "../x"}, {"node": "99001", "callsign": "a;b"}, {"node": "99001", "callsign": "x" * 20}, {}])
def test_bad_requests_are_refused_before_anything_is_read(site, body):
    client, etc, *_ = site
    before = snapshot_of(etc)
    assert client.post("/api/allstar/import", json=body).status_code == 422
    assert snapshot_of(etc) == before


def test_apply_does_not_log_a_password_even_at_debug_level(site, caplog):
    client, *_ = site
    client.get("/api/allstar/import")
    client.post("/api/allstar/import", json={"node": "99001"})
    assert PASSWORD not in caplog.text and PASSWORD not in json.dumps([r.getMessage() for r in caplog.records])
    assert any("imported node 99001" in r.getMessage() for r in caplog.records)  # it does say what it did, without the secret


# -- who may -----------------------------------------------------------------------------------------------------------


def test_only_an_admin_may_preview_or_apply(tmp_path, monkeypatch):
    source = source_copy(tmp_path)
    monkeypatch.setenv("MOREOPENREPEATER_ALLSTAR_IMPORT_DIR", str(source))
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "rpt.conf").write_text(CONTAINER_RPT)
    (etc / "modules.conf").write_text(CONTAINER_MODULES)
    (etc / "rpt_http_registrations.conf").write_text(CONTAINER_REG)
    service = RepeaterService(config=RepeaterConfig())
    node = AllStarNode(AsteriskFiles(str(etc), backup_dir=tmp_path / "backups"), AMI, AllStarAudio(service, None), listen=("127.0.0.1", 0), ami_factory=FakeAMI().client)
    users = UserStore(reserved_username="admin")
    users.add("olly", "password1", "operator")
    users.add("val", "password1", "viewer")
    app = create_app(service=service, start_background_tick=False, assets_store=AudioAssetStore(Path(tempfile.mkdtemp()) / "audio"),
                     log_path=tmp_path / "t.log", allstar_node=node, auth_settings=AuthSettings("admin", "hunter2"), users=users)
    import base64

    def basic(user, password):
        return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}

    with TestClient(app) as client:
        assert client.get("/api/allstar/import").status_code == 401  # signed out
        assert client.post("/api/allstar/import", json={"node": "99001"}).status_code == 401
        for who in (basic("olly", "password1"), basic("val", "password1")):
            assert client.get("/api/allstar/import", headers=who).status_code == 403
            assert client.post("/api/allstar/import", json={"node": "99001"}, headers=who).status_code == 403
        assert (etc / "rpt.conf").read_text() == CONTAINER_RPT
        admin = basic("admin", "hunter2")
        assert client.get("/api/allstar/import", headers=admin).status_code == 200
        assert client.post("/api/allstar/import", json={"node": "99001"}, headers=admin).status_code == 200
