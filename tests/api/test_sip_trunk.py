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
    SipTrunk,
    TrunkSettings,
    parse_config,
)

SETTINGS = PatchSettings("127.0.0.1", 5038, "admin", "secret", "127.0.0.1", 0, "127.0.0.1:9092")


class FakeAsterisk:
    """Config files as [section name, [(key, value)]] lists, loaded modules,
    and just enough of AMI's config, module and PJSIP actions."""

    def __init__(self, files=None, loaded=(), registration="Registered", contact="Reachable"):
        self.files = files if files is not None else {"pjsip.conf": [], "modules.conf": [["modules", [("autoload", "no")]]]}
        self.loaded = set(loaded)
        self.unloadable = set()
        self.registration = registration
        self.contact = contact
        self.actions = []
        self.reloads = 0

    def client(self, *args):
        return FakeAMI(self)


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
            for i, (name, lines) in enumerate(a.files[fields["Filename"]]):
                response[f"Category-{i:06d}"] = name
                for j, (key, value) in enumerate(lines):
                    response[f"Line-{i:06d}-{j:06d}"] = f"{key}={value}"
            return response
        if action == "CreateConfig":
            a.files[fields["Filename"]] = []
            return ok
        if action == "UpdateConfig":
            self._update(a.files[fields["SrcFilename"]], fields)
            return ok
        if action == "PJSIPRegister":
            return ok
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
    assert names == ["my-phone", "mor-transport-udp", "mor-trunk-auth", "mor-trunk-aor", "mor-trunk", "mor-trunk-reg"]
    assert asterisk.files["pjsip.conf"][0] == other
    assert section(asterisk, "mor-trunk-auth")["password"] == "pa;ss"  # Asterisk escapes it when saving
    assert section(asterisk, "mor-trunk-aor")["contact"] == "sip:chicago.voip.ms:5080"
    assert section(asterisk, "mor-trunk-reg")["client_uri"] == "sip:123456_rpt@chicago.voip.ms:5080"
    assert section(asterisk, "mor-trunk")["transport"] == "mor-transport-udp"
    assert asterisk.reloads == 1
    assert any(a["Action"] == "PJSIPRegister" for a in asterisk.actions)
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
    }
    assert (status["registration"], status["reachability"]) == ("Registered", "Reachable")


def test_resaving_replaces_the_trunk_keeps_the_password_and_reuses_the_transport():
    asterisk = FakeAsterisk(loaded=MODULES)
    asterisk.files["pjsip.conf"] = [["transport-udp", [("type", "transport"), ("protocol", "udp"), ("bind", "0.0.0.0")]]]
    trunk = SipTrunk(SETTINGS, asterisk.client)
    run(trunk.save(TrunkSettings("sip.telnyx.com", "alice", "secret")))
    run(trunk.save(TrunkSettings("abc.pstn.twilio.com", "bob", None, transport="udp", auth_username="bob-auth", registers=False)))

    names = [name for name, _ in asterisk.files["pjsip.conf"]]
    assert names == ["transport-udp", "mor-trunk-auth", "mor-trunk-aor", "mor-trunk"]
    assert section(asterisk, "mor-trunk-auth") == {
        "type": "auth", "auth_type": "userpass", "username": "bob-auth", "password": "secret",
    }
    assert section(asterisk, "mor-trunk")["transport"] == "transport-udp"
    status = run(trunk.status())
    assert status["trunk"]["registers"] is False and status["registration"] is None
    assert (status["trunk"]["username"], status["trunk"]["auth_username"]) == ("bob-auth", "")


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


def test_dial_string_names_the_endpoint():
    assert DIAL_STRING == "PJSIP/{number}@mor-trunk"
    assert "chan_pjsip.so" in SIP_MODULES
