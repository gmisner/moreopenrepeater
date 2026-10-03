import socketserver
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from controller.macros import Macro
from controller.state_machine import RepeaterConfig

from api.app import create_app
from api.remote_base import RemoteBase
from api.rig_control import RigError, Rigctld, Tuning, parse_address
from api.service import RepeaterService


class FakeRigctld(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, refuse=()):
        self.received = []
        self.refuse = set(refuse)
        server = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                for raw in self.rfile:
                    line = raw.decode().strip()
                    server.received.append(line)
                    code = -11 if line.lstrip("+").split()[0] in server.refuse else 0
                    self.wfile.write(f"{line}:\nRPRT {code}\n".encode())

        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def address(self):
        return f"127.0.0.1:{self.server_address[1]}"


def test_parse_address():
    assert parse_address("localhost:4532") == ("localhost", 4532)
    assert parse_address("rig.local") == ("rig.local", 4532)
    assert parse_address("[::1]:4533") == ("::1", 4533)


def test_tune_sends_extended_commands():
    server = FakeRigctld()
    try:
        Rigctld(server.address).tune(Tuning(146.94, "minus", 0.6, 100.0))
    finally:
        server.shutdown()
    assert server.received == ["+F 146940000", "+M FM 0", "+R -", "+O 600000", "+C 1000", "+U TONE 1"]


def test_tune_ignores_an_unsupported_tone_but_not_a_refused_frequency():
    server = FakeRigctld(refuse={"U", "R"})
    try:
        Rigctld(server.address).tune(Tuning(146.52, "simplex", 0.0, None))
        server.refuse = {"F"}
        with pytest.raises(RigError, match="refused F 146520000"):
            Rigctld(server.address).tune(Tuning(146.52, "simplex", 0.0, None))
    finally:
        server.shutdown()


def test_unreachable_rigctld():
    with pytest.raises(RigError, match="couldn't reach rigctld"):
        Rigctld("127.0.0.1:1", timeout=0.5).tune(Tuning(146.52, "simplex", 0.0, None))


class FakeRig:
    def __init__(self, address, log, fail=False):
        self.log, self.fail = log, fail
        self.address = address

    def tune(self, tuning):
        if self.fail:
            raise RigError("no answer")
        self.log.append(tuning)


MACROS = [
    Macro("*40", "on", action="link_radio_on"),
    Macro("*41", "tune", action="remote_tune"),
    Macro("*42", "minus", command="minus", action="remote_shift"),
    Macro("*43", "tone", action="remote_tone"),
    Macro("*44", "status", action="remote_status"),
]


def make(fail=False, **config):
    service = RepeaterService(config=RepeaterConfig(**{"link_radio_mode": "remote_base", **config}), macros=MACROS, clock=lambda: 0.0)
    tuned = []
    remote = RemoteBase(service, lambda address: FakeRig(address, tuned, fail))
    remote.attach(None)
    service.heard = 0
    return service, remote, tuned


def dial(service, digits):
    for digit in digits:
        service.simulate_dtmf(digit)


def spoken(service):
    said = service.controller.queued_announcements[service.heard:]
    service.heard += len(said)
    return said


def wait_for(condition):
    for _ in range(200):
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_saved_tuning_is_sent_on_start():
    _, remote, tuned = make()
    assert tuned == [Tuning(146.52, "simplex", 0.0, None)]
    assert remote.status() == {"rig_error": None, "tuned": True}


def test_dtmf_tuning():
    service, _, tuned = make()
    dial(service, "*41146*94#")
    assert spoken(service) == ["tts:Remote base 146.94, simplex, no tone."]
    dial(service, "*42")
    assert spoken(service) == ["tts:Remote base 146.94, minus offset, no tone."]
    assert tuned[-1] == Tuning(146.94, "minus", 0.6, None)
    dial(service, "*431000#")
    assert spoken(service) == ["tts:Remote base 146.94, minus offset, tone 100.0."]
    assert service.config.remote_base_tone_hz == 100.0
    dial(service, "*430#")
    assert service.config.remote_base_tone_hz is None


def test_refuses_frequencies_outside_the_bands():
    service, _, tuned = make()
    dial(service, "*41160*0#")
    assert spoken(service) == ["tts:160.0 is outside the remote base's bands."]
    dial(service, "*41144*1#*42")  # 144.1 minus 600 kHz would transmit on 143.5
    assert spoken(service)[-1] == "tts:144.1 is outside the remote base's bands."
    assert service.config.remote_base_shift == "simplex"


def test_bad_entries_and_a_silent_radio():
    service, remote, _ = make(fail=True)
    dial(service, "*4114#")
    assert spoken(service) == ["tts:That frequency is not valid."]
    dial(service, "*431234#")
    assert spoken(service) == ["tts:That tone is not valid."]
    dial(service, "*41147*0#")
    assert spoken(service) == ["tts:The remote base radio did not respond."]
    assert remote.status() == {"rig_error": "no answer", "tuned": False}


def test_on_and_status_speak_the_frequency():
    service, _, _ = make(remote_base_shift="minus", remote_base_mhz=146.94, remote_base_tone_hz=88.5)
    dial(service, "*44")
    assert spoken(service) == ["tts:Remote base off, 146.94, minus offset, tone 88.5."]
    dial(service, "*40")
    assert spoken(service) == ["tts:Remote base on, 146.94, minus offset, tone 88.5."]


def test_link_mode_and_no_rig_control():
    service, _, tuned = make(link_radio_mode="link")
    dial(service, "*44")
    assert spoken(service) == ["tts:The remote base is not set up."]
    assert tuned == []
    service, _, tuned = make(remote_base_rigctld="")
    dial(service, "*41146*94#")
    assert spoken(service) == ["tts:The remote base has no radio control."]


def test_settings_and_retune_through_the_api():
    service = RepeaterService(config=RepeaterConfig(), clock=lambda: 0.0)
    tuned = []
    remote = RemoteBase(service, lambda address: FakeRig(address, tuned))
    tmp = Path(tempfile.mkdtemp())
    with TestClient(create_app(service=service, remote_base=remote, start_background_tick=False, log_path=tmp / "t.log")) as client:
        response = client.put("/api/config", json={"link_radio_mode": "remote_base", "remote_base_mhz": 446.0, "remote_base_tone_hz": 100.0})
        assert response.status_code == 200
        wait_for(lambda: tuned and tuned[-1] == Tuning(446.0, "simplex", 0.0, 100.0))
        assert client.put("/api/config", json={"remote_base_tone_hz": 101.0}).status_code == 422
        assert client.put("/api/config", json={"remote_base_ranges": "148-144"}).status_code == 422
        assert client.put("/api/config", json={"remote_base_rigctld": "rig; rm -rf"}).status_code == 422
        assert client.post("/api/macros", json={"pattern": "*42", "action": "remote_shift", "command": "up"}).status_code == 422
        assert client.post("/api/macros", json={"pattern": "*41", "action": "remote_tune", "needs_code": True}).status_code == 422
        count = len(tuned)
        status = client.post("/api/link-radio/tune").json()
        assert status["mode"] == "remote_base"
        wait_for(lambda: len(tuned) == count + 1)
