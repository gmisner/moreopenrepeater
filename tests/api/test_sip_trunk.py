import asyncio
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.autopatch import Autopatch, CallRecord, PatchSettings
from api.service import RepeaterService
from controller.state_machine import RepeaterConfig
from api.sip_trunk import (
    DIAL_STRING,
    MODULES,
    SIP_MODULES,
    AsteriskSetupError,
    TWILIO_SIGNALING,
    SipTrunk,
    TrunkSettings,
    incoming_dialplan,
    parse_config,
)

SETTINGS = PatchSettings("127.0.0.1", 5038, "admin", "secret", "127.0.0.1", 0, "127.0.0.1:9092")


class FakeAsterisk:
    """Config files as [section name, [(key, value)]] lists, loaded modules,
    and just enough of AMI's config, module and PJSIP actions."""

    def __init__(self, files=None, loaded=(), registration="Registered", contact="Reachable", asl3=True):
        self.files = files if files is not None else {
            "pjsip.conf": [],
            "modules.conf": [["modules", [("autoload", "no")]]],
            "extensions.conf": [["default", [("exten", "i,1,Hangup")]]],
        }
        self.asl3 = asl3  # extensions.conf includes custom/extensions.conf, which needn't exist
        self.dialplan = set()  # contexts loaded at the last dialplan reload
        self.loaded = set(loaded)
        self.unloadable = set()
        self.registration = registration
        self.contact = contact
        self.actions = []
        self.reloads = 0
        self.restarts = 0
        self.can_restart = True

    def client(self, *args):
        return FakeAMI(self)

    def dialplan_sections(self):
        """extensions.conf as Asterisk loads it, includes and all."""
        included = self.files.get("custom/extensions.conf", []) if self.asl3 else []
        return self.files["extensions.conf"] + included


class FakeAMI:
    def __init__(self, asterisk):
        self.asterisk = asterisk
        self._events = asyncio.Queue()

    async def connect(self):
        pass

    async def close(self):
        self._events.put_nowait(None)

    async def events(self):
        while (event := await self._events.get()) is not None:
            yield event

    async def send_action(self, fields):
        a = self.asterisk
        a.actions.append(fields)
        assert len(fields) <= 128, "Asterisk drops messages this long"
        action = fields["Action"]
        ok = {"Response": "Success", "ActionID": fields.get("ActionID", "")}
        if action == "ModuleCheck":
            return ok if fields["Module"] in a.loaded else {"Response": "Error", "Message": "Module not loaded"}
        if action == "ModuleLoad":
            if fields["LoadType"] == "reload":
                if fields["Module"] == "pbx_config.so":
                    a.dialplan = {name for name, _ in a.dialplan_sections()}
                else:
                    a.reloads += 1
                return ok
            if fields["Module"] in a.unloadable:
                return {"Response": "Error", "Message": "Could not load module."}
            a.loaded.add(fields["Module"])
            return ok
        if action == "GetConfig":
            if fields["Filename"] not in a.files:
                return {"Response": "Error", "Message": "Config file not found"}
            response = dict(ok)
            sections = a.dialplan_sections() if fields["Filename"] == "extensions.conf" else a.files[fields["Filename"]]
            for i, (name, lines) in enumerate(sections):
                response[f"Category-{i:06d}"] = name
                for j, (key, value) in enumerate(lines):
                    response[f"Line-{i:06d}-{j:06d}"] = f"{key}={value}"
            return response
        if action == "CreateConfig":
            if fields["Filename"].startswith("custom/") and not a.asl3:
                return {"Response": "Error", "Message": "Failed to create file"}
            a.files[fields["Filename"]] = []
            return ok
        if action == "ShowDialPlan":
            if fields["Context"] not in a.dialplan:
                return {"Response": "Error", "Message": f"Did not find context {fields['Context']}"}
            self._events.put_nowait({"Event": "ListDialplan", "ActionID": fields["ActionID"], "Context": fields["Context"]})
            self._events.put_nowait({"Event": "ShowDialPlanComplete", "ActionID": fields["ActionID"], "EventList": "Complete"})
            return {**ok, "EventList": "start"}
        if action == "UpdateConfig":
            self._update(a.files[fields["SrcFilename"]], fields)
            return ok
        if action == "PJSIPRegister":
            return ok
        if action == "CoreStatus":
            return {**ok, "CoreStartupDate": "2026-10-04", "CoreStartupTime": f"10:00:{a.restarts:02d}"}
        if action == "Command" and fields["Command"] == "core restart now":
            if not a.can_restart:
                return {"Response": "Error", "Message": "Permission denied"}
            a.restarts += 1
            raise ConnectionResetError("Asterisk closed the connection")
        if action == "PJSIPShowRegistrationsOutbound":
            regs = [n for n, lines in a.files["pjsip.conf"] if ("type", "registration") in lines]
            for name in regs:
                self._events.put_nowait({"Event": "OutboundRegistrationDetail", "ActionID": fields["ActionID"], "ObjectName": name, "Status": a.registration})
            self._events.put_nowait({"Event": "OutboundRegistrationDetailComplete", "ActionID": fields["ActionID"], "EventList": "Complete"})
            return {**ok, "EventList": "start"}
        if action == "PJSIPShowEndpoint":
            self._events.put_nowait({"Event": "EndpointDetail", "ActionID": fields["ActionID"]})
            self._events.put_nowait({"Event": "ContactStatusDetail", "ActionID": fields["ActionID"], "Status": a.contact})
            self._events.put_nowait({"Event": "EndpointDetailComplete", "ActionID": fields["ActionID"], "EventList": "Complete"})
            return {**ok, "EventList": "start"}
        raise AssertionError(f"unexpected action {action}")

    @staticmethod
    def _update(sections, fields):
        n = 0
        while (action := fields.get(f"Action-{n:06d}")) is not None:
            cat = fields[f"Cat-{n:06d}"]
            var, value, match = (fields.get(f"{k}-{n:06d}") for k in ("Var", "Value", "Match"))
            if action == "NewCat":
                sections.append([cat, []])
            elif action == "DelCat":
                sections.remove(next(s for s in sections if s[0] == cat))
            elif action == "Append":
                next(s for s in reversed(sections) if s[0] == cat)[1].append((var, value))
            elif action == "Delete":
                lines = next(s for s in sections if s[0] == cat)[1]
                lines.remove(next(line for line in lines if line == (var, match)))
            n += 1


def section(asterisk, name, file="pjsip.conf"):
    return dict(next(lines for n, lines in asterisk.files[file] if n == name))


def run(coroutine):
    return asyncio.run(coroutine)


def test_parse_config():
    response = {
        "Response": "Success",
        "Category-000000": "a",
        "Line-000000-000000": "type=aor",
        "Line-000000-000001": "contact=sip:x=y",
        "Category-000001": "b",
    }
    assert parse_config(response) == [("a", [("type", "aor"), ("contact", "sip:x=y")]), ("b", [])]


def test_without_ami_settings():
    status = run(SipTrunk(None).status())
    assert status["configured"] is False and status["trunk"] is None
    with pytest.raises(AsteriskSetupError):
        run(SipTrunk(None).enable_modules())


def test_enabling_modules_loads_them_in_order_and_at_startup():
    asterisk = FakeAsterisk(loaded={"codec_ulaw.so"})
    asterisk.files["modules.conf"][0][1] += [("load", "res_pjsip.so"), ("noload", "chan_pjsip.so")]
    trunk = SipTrunk(SETTINGS, asterisk.client)
    assert run(trunk.status())["missing_modules"] == [m for m in MODULES if m != "codec_ulaw.so"]

    run(trunk.enable_modules())

    loads = [a["Module"] for a in asterisk.actions if a["Action"] == "ModuleLoad"]
    assert loads == [m for m in MODULES if m != "codec_ulaw.so"]
    modules_conf = asterisk.files["modules.conf"][0][1]
    assert ("noload", "chan_pjsip.so") not in modules_conf
    assert [v for k, v in modules_conf if k == "load"].count("res_pjsip.so") == 1
    assert {v for k, v in modules_conf if k == "load"} == set(MODULES)
    assert run(trunk.status())["missing_modules"] == []


def test_autoloading_asterisk_needs_no_modules_conf_changes():
    asterisk = FakeAsterisk()
    asterisk.files["modules.conf"] = [["modules", [("autoload", "yes")]]]
    run(SipTrunk(SETTINGS, asterisk.client).enable_modules())
    assert asterisk.files["modules.conf"] == [["modules", [("autoload", "yes")]]]


def test_a_module_that_wont_load_is_reported():
    asterisk = FakeAsterisk()
    asterisk.unloadable.add("chan_pjsip.so")
    with pytest.raises(AsteriskSetupError, match="chan_pjsip.so"):
        run(SipTrunk(SETTINGS, asterisk.client).enable_modules())


def test_saving_needs_sip_turned_on():
    with pytest.raises(AsteriskSetupError, match="Turn on SIP"):
        run(SipTrunk(SETTINGS, FakeAsterisk().client).save(TrunkSettings("sip.telnyx.com", "user", "pw")))


def test_save_writes_the_trunk_and_keeps_other_sections():
    other = ["my-phone", [("type", "endpoint"), ("context", "home")]]
    asterisk = FakeAsterisk(loaded=MODULES)
    asterisk.files["pjsip.conf"] = [other]
    trunk = SipTrunk(SETTINGS, asterisk.client)

    run(trunk.save(TrunkSettings("chicago.voip.ms", "123456_rpt", "pa;ss", port=5080)))

    names = [name for name, _ in asterisk.files["pjsip.conf"]]
    assert names == [
        "my-phone", "mor-transport-udp", "mor-trunk-auth", "mor-trunk-aor", "mor-trunk", "mor-trunk-identify", "mor-trunk-reg",
    ]
    assert asterisk.files["pjsip.conf"][0] == other
    assert section(asterisk, "mor-trunk-auth")["password"] == "pa;ss"  # Asterisk escapes it when saving
    assert section(asterisk, "mor-trunk-aor")["contact"] == "sip:chicago.voip.ms:5080"
    assert section(asterisk, "mor-trunk-reg")["client_uri"] == "sip:123456_rpt@chicago.voip.ms:5080"
    assert section(asterisk, "mor-trunk")["transport"] == "mor-transport-udp"
    updates = [a for a in asterisk.actions if a["Action"] == "UpdateConfig"]
    assert len(updates) > 1  # split under Asterisk's header limit

    status = run(trunk.status())
    assert status["trunk"] == {
        "server": "chicago.voip.ms",
        "port": 5080,
        "transport": "udp",
        "username": "123456_rpt",
        "auth_username": "",
        "has_password": True,
        "registers": True,
        "incoming_from": [],
        "answers_calls": True,
    }
    assert (status["registration"], status["reachability"]) == ("Registered", "Reachable")


def test_resaving_replaces_the_trunk_keeps_the_password_and_reuses_the_transport():
    asterisk = FakeAsterisk(loaded=MODULES)
    asterisk.files["pjsip.conf"] = [["transport-udp", [("type", "transport"), ("protocol", "udp"), ("bind", "0.0.0.0")]]]
    trunk = SipTrunk(SETTINGS, asterisk.client)
    run(trunk.save(TrunkSettings("sip.telnyx.com", "alice", "secret")))
    run(trunk.save(TrunkSettings("abc.pstn.twilio.com", "bob", None, transport="udp", auth_username="bob-auth", registers=False)))

    names = [name for name, _ in asterisk.files["pjsip.conf"]]
    assert names == ["transport-udp", "mor-trunk-auth", "mor-trunk-aor", "mor-trunk", "mor-trunk-identify"]
    assert section(asterisk, "mor-trunk-auth") == {
        "type": "auth", "auth_type": "userpass", "username": "bob-auth", "password": "secret",
    }
    assert section(asterisk, "mor-trunk")["transport"] == "transport-udp"
    status = run(trunk.status())
    assert status["trunk"]["registers"] is False and status["registration"] is None
    assert (status["trunk"]["username"], status["trunk"]["auth_username"]) == ("bob-auth", "")
    assert (asterisk.reloads, asterisk.restarts) == (2, 0)
    assert any(a["Action"] == "PJSIPRegister" for a in asterisk.actions)


def test_a_new_transport_restarts_asterisk():
    """PJSIP's resolver only uses the transports res_pjsip found when it
    loaded, so a trunk on a new one couldn't look up its server until a restart."""
    asterisk = FakeAsterisk(loaded=MODULES)
    trunk = SipTrunk(SETTINGS, asterisk.client)

    run(trunk.save(TrunkSettings("moreopenrepeater.pstn.twilio.com", "user", "pw", registers=False)))

    assert (asterisk.restarts, asterisk.reloads) == (1, 0)
    commands = [a["Action"] for a in asterisk.actions]
    assert commands.index("Command") > max(i for i, c in enumerate(commands) if c == "UpdateConfig")
    assert commands.count("CoreStatus") >= 2  # waited for it to come back
    assert run(trunk.status())["trunk"]["server"] == "moreopenrepeater.pstn.twilio.com"


def test_a_login_that_cant_restart_asterisk_says_so():
    asterisk = FakeAsterisk(loaded=MODULES)
    asterisk.can_restart = False

    with pytest.raises(AsteriskSetupError, match="restart Asterisk"):
        run(SipTrunk(SETTINGS, asterisk.client).save(TrunkSettings("sip.telnyx.com", "alice", "pw")))

    assert "mor-trunk" in [name for name, _ in asterisk.files["pjsip.conf"]]


def test_tcp_goes_into_the_uris():
    asterisk = FakeAsterisk(loaded=MODULES)
    run(SipTrunk(SETTINGS, asterisk.client).save(TrunkSettings("sip.example.com", "u", "p", transport="tcp")))
    assert section(asterisk, "mor-trunk-aor")["contact"] == "sip:sip.example.com;transport=tcp"
    assert section(asterisk, "mor-transport-tcp")["protocol"] == "tcp"
    assert run(SipTrunk(SETTINGS, asterisk.client).status())["trunk"]["transport"] == "tcp"


def test_first_save_needs_a_password():
    asterisk = FakeAsterisk(loaded=MODULES)
    with pytest.raises(AsteriskSetupError, match="password"):
        run(SipTrunk(SETTINGS, asterisk.client).save(TrunkSettings("sip.telnyx.com", "alice")))


def test_missing_pjsip_conf_is_created():
    asterisk = FakeAsterisk(loaded=MODULES)
    del asterisk.files["pjsip.conf"]
    run(SipTrunk(SETTINGS, asterisk.client).save(TrunkSettings("sip.telnyx.com", "alice", "pw")))
    assert "mor-trunk" in [name for name, _ in asterisk.files["pjsip.conf"]]


def test_remove_deletes_only_the_trunk():
    asterisk = FakeAsterisk(loaded=MODULES)
    trunk = SipTrunk(SETTINGS, asterisk.client)
    run(trunk.save(TrunkSettings("sip.telnyx.com", "alice", "pw")))
    run(trunk.remove())
    assert [name for name, _ in asterisk.files["pjsip.conf"]] == ["mor-transport-udp"]
    assert run(trunk.status())["trunk"] is None


def test_unreachable_asterisk_is_an_error_in_the_status():
    class Down(FakeAMI):
        async def connect(self):
            raise ConnectionRefusedError("refused")

    status = run(SipTrunk(SETTINGS, lambda *a: Down(None)).status())
    assert "Couldn't reach Asterisk" in status["error"]


def make_client(asterisk):
    service = RepeaterService(config=RepeaterConfig(autopatch_dial_string="Local/{number}@test"), clock=lambda: 0.0)
    tmp_dir = Path(tempfile.mkdtemp())
    autopatch = Autopatch(service, SETTINGS)
    app = create_app(
        service=service,
        start_background_tick=False,
        assets_store=AudioAssetStore(tmp_dir / "audio"),
        log_path=tmp_dir / "test.log",
        autopatch=autopatch,
        sip_trunk=SipTrunk(SETTINGS, asterisk.client),
    )
    return TestClient(app), service, autopatch


TRUNK_BODY = {"server": "sip.telnyx.com", "username": "alice", "password": "s3cret;x", "ten_digit_prefix": "+1"}


def test_routes_set_up_the_trunk_and_point_autopatch_at_it():
    asterisk = FakeAsterisk()
    client, service, _ = make_client(asterisk)
    body = client.get("/api/autopatch/trunk").json()
    assert body["in_use"] is False and "chan_pjsip.so" in body["missing_modules"]

    assert client.put("/api/autopatch/trunk", json=TRUNK_BODY).status_code == 502  # SIP is off
    assert client.post("/api/autopatch/trunk/modules").json()["missing_modules"] == []

    response = client.put("/api/autopatch/trunk", json=TRUNK_BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["trunk"]["has_password"] is True and "s3cret" not in response.text
    assert body["in_use"] is True and body["ten_digit_prefix"] == "+1"
    assert service.config.autopatch_dial_string == DIAL_STRING
    assert service.config.autopatch_ten_digit_prefix == "+1"

    assert client.delete("/api/autopatch/trunk").json()["trunk"] is None


def test_trunk_changes_wait_for_the_call_to_end():
    client, _, autopatch = make_client(FakeAsterisk(loaded=MODULES))
    autopatch.call = CallRecord(number="911", actor="DTMF", started_at=0.0)
    response = client.put("/api/autopatch/trunk", json=TRUNK_BODY)
    assert response.status_code == 409 and response.json()["detail"] == "Hang up the call first."


def test_trunk_request_validation():
    client, _, _ = make_client(FakeAsterisk(loaded=MODULES))
    for bad in (
        {"server": "sip telnyx.com"},
        {"server": "sip.telnyx.com\r\nAction: Command"},
        {"username": "al ice"},
        {"username": "alice@sip.telnyx.com"},
        {"password": "a\nb"},
        {"password": " leading"},
        {"transport": "tls"},
        {"port": 0},
        {"ten_digit_prefix": "011"},
    ):
        assert client.put("/api/autopatch/trunk", json={**TRUNK_BODY, **bad}).status_code == 422, bad


def test_calls_in_match_the_trunk_from_the_server_and_listed_sources():
    asterisk = FakeAsterisk(loaded=MODULES)
    trunk = SipTrunk(SETTINGS, asterisk.client)
    run(trunk.save(TrunkSettings("abc.pstn.twilio.com", "bob", "pw", registers=False, incoming_from=TWILIO_SIGNALING)))
    identify = next(lines for name, lines in asterisk.files["pjsip.conf"] if name == "mor-trunk-identify")
    assert identify[:2] == [("type", "identify"), ("endpoint", "mor-trunk")]
    assert [v for k, v in identify if k == "match"] == ["abc.pstn.twilio.com", *TWILIO_SIGNALING]
    assert section(asterisk, "mor-trunk")["context"] == "mor-incoming"
    assert run(trunk.status())["trunk"]["incoming_from"] == list(TWILIO_SIGNALING)


def test_calls_in_dialplan_goes_in_asl3s_custom_file():
    asterisk = FakeAsterisk(loaded=MODULES)
    stock = [list(s) for s in asterisk.files["extensions.conf"]]
    trunk = SipTrunk(SETTINGS, asterisk.client)
    run(trunk.save(TrunkSettings("sip.telnyx.com", "alice", "pw")))
    run(trunk.save(TrunkSettings("sip.telnyx.com", "alice", None)))

    assert asterisk.files["extensions.conf"] == stock
    assert asterisk.files["custom/extensions.conf"] == [["mor-incoming", incoming_dialplan("127.0.0.1:9092")]]
    assert "mor-incoming" in asterisk.dialplan
    assert ("same", "n,AudioSocket(${MOR_ID:0:8}-${MOR_ID:8:4}-${MOR_ID:12:4}-${MOR_ID:16:4}-${MOR_ID:20:12},127.0.0.1:9092)") in (
        incoming_dialplan("127.0.0.1:9092")
    )
    assert run(trunk.status())["trunk"]["answers_calls"] is True

    run(trunk.remove())
    assert asterisk.files["custom/extensions.conf"] == []
    assert "mor-incoming" not in asterisk.dialplan


def test_calls_in_dialplan_falls_back_to_extensions_conf():
    asterisk = FakeAsterisk(loaded=MODULES, asl3=False)
    run(SipTrunk(SETTINGS, asterisk.client).save(TrunkSettings("sip.telnyx.com", "alice", "pw")))
    assert [name for name, _ in asterisk.files["extensions.conf"]] == ["default", "mor-incoming"]
    assert "custom/extensions.conf" not in asterisk.files
    assert "mor-incoming" in asterisk.dialplan


def test_a_line_saved_before_calls_in_doesnt_answer():
    asterisk = FakeAsterisk(loaded=MODULES)
    trunk = SipTrunk(SETTINGS, asterisk.client)
    run(trunk.save(TrunkSettings("sip.telnyx.com", "alice", "pw")))
    del asterisk.files["custom/extensions.conf"]
    asterisk.dialplan.clear()
    assert run(trunk.status())["trunk"]["answers_calls"] is False


def test_incoming_from_validation():
    client, _, _ = make_client(FakeAsterisk(loaded=MODULES))
    assert client.post("/api/autopatch/trunk/modules").status_code == 200
    ok = client.put("/api/autopatch/trunk", json={**TRUNK_BODY, "incoming_from": ["54.172.60.0/30", "sip.example.com", "2001:db8::/32"]})
    assert ok.status_code == 200 and ok.json()["trunk"]["incoming_from"] == ["54.172.60.0/30", "sip.example.com", "2001:db8::/32"]
    for bad in (["a b"], ["1.2.3.4,5.6.7.8"], ["x\r\nAction: Command"], ["1.2.3.4"] * 33):
        assert client.put("/api/autopatch/trunk", json={**TRUNK_BODY, "incoming_from": bad}).status_code == 422, bad


def test_dial_string_names_the_endpoint():
    assert DIAL_STRING == "PJSIP/{number}@mor-trunk"
    assert "chan_pjsip.so" in SIP_MODULES
