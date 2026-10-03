import dataclasses
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.link_radio import LinkRadio, setup_problem
from api.live_audio import LiveAudio
from api.service import RepeaterService
from audio_io.engine import AudioEngine
from controller.state_machine import RECEIVING, RepeaterConfig
from playout.renderer import ClipRenderer

from test_link_radio import CONFIG as LINK_CONFIG
from test_live_audio import FakePin, FakeStream, ImmediateLoop, feed, tone

RATE = 16000
USB0 = "/dev/ttyUSB0"


class FakeSerial:
    def __init__(self, fail=()):
        self.lines = {}
        self.fail = fail

    def open(self, device, line, *, output, active_low):
        if (device, line) in self.fail:
            raise PermissionError(13, "Permission denied")
        pin = FakePin(line, output, active_low)
        self.lines[(device, line)] = pin
        return pin


def make(config, serial=None):
    tmp = Path(tempfile.mkdtemp())
    renderer = ClipRenderer(AudioAssetStore(tmp / "audio").path_for, tts=None, sample_rate=RATE)
    service = RepeaterService(config=config, clock=lambda: 0.0, renderer=renderer)
    serial = serial or FakeSerial()
    engines = []

    def engine_factory(*args, **kwargs):
        engines.append(AudioEngine(*args, stream_factory=FakeStream, **kwargs))
        return engines[-1]

    live = LiveAudio(service, renderer, engine_factory=engine_factory, open_serial=serial.open)
    live.attach(ImmediateLoop())
    link = LinkRadio(
        service, renderer, sending=lambda: False, attach_port=live.set_port, engine_factory=engine_factory,
        open_serial=serial.open, clock=lambda: 0.0,
    )
    link.attach(ImmediateLoop())
    return live, link, service, serial, engines


SERIAL_REPEATER = RepeaterConfig(
    audio_enabled=True,
    cos_source="serial", cos_serial_device=USB0, cos_serial_line="dcd", cos_polarity="high",
    ptt_output="serial", ptt_serial_device=USB0, ptt_serial_line="rts", ptt_polarity="high",
)


def test_serial_lines_supply_cos_and_ptt():
    live, _link, service, serial, _engines = make(SERIAL_REPEATER)
    engine = live.engine
    ptt, cos = serial.lines[(USB0, "rts")], serial.lines[(USB0, "dcd")]
    assert (ptt.output, ptt.active_low, cos.output, cos.active_low) == (True, False, False, False)
    assert engine.processor.settings.cos_source == "external"
    assert live.status()["hardware_ptt"] == "serial"

    cos.active = True
    engine.processor.set_external_cos(engine._cos_input())
    feed(engine, tone(level=0.0, blocks=2))
    assert service.controller.state == RECEIVING
    assert ptt.writes == [True]

    service.update_config(audio_enabled=False)
    assert ptt.writes[-1] is False
    assert ptt.closed and cos.closed


def test_changing_the_serial_line_restarts_the_engine():
    live, _link, service, serial, engines = make(SERIAL_REPEATER)
    service.update_config(ptt_serial_line="dtr")
    assert len(engines) == 2 and live.engine is engines[1]
    assert serial.lines[(USB0, "rts")].closed and (USB0, "dtr") in serial.lines


def test_serial_without_a_device_is_an_error():
    live, _link, _service, _serial, engines = make(dataclasses.replace(SERIAL_REPEATER, ptt_serial_device=""))
    assert engines == []
    assert "no serial port is chosen" in live.status()["error"]


def test_serial_open_failure_is_reported_and_releases_the_other_line():
    serial = FakeSerial(fail=((USB0, "dcd"),))
    live, _link, _service, serial, engines = make(SERIAL_REPEATER, serial)
    assert engines == []
    assert live.status()["error"] == f"couldn't open {USB0} (DCD) for COS: Permission denied"
    assert serial.lines[(USB0, "rts")].closed


def test_link_radio_can_use_other_lines_of_the_same_port():
    config = dataclasses.replace(
        LINK_CONFIG,
        **{k: getattr(SERIAL_REPEATER, k) for k in ("cos_source", "cos_serial_device", "cos_serial_line", "ptt_output",
                                                   "ptt_serial_device", "ptt_serial_line")},
        link_radio_cos="serial", link_radio_cos_serial_device=USB0, link_radio_cos_serial_line="cts",
        link_radio_ptt="serial", link_radio_ptt_serial_device=USB0, link_radio_ptt_serial_line="dtr",
        link_radio_ptt_polarity="low",
    )
    _live, link, _service, serial, _engines = make(config)

    assert link.engine is not None, link.error
    assert serial.lines[(USB0, "dtr")].active_low is True
    assert link.engine.processor.settings.cos_source == "external"


def test_link_radio_cannot_take_a_line_the_repeater_uses():
    config = dataclasses.replace(
        LINK_CONFIG, ptt_output="serial", ptt_serial_device=USB0, ptt_serial_line="rts",
        link_radio_ptt="serial", link_radio_ptt_serial_device=USB0, link_radio_ptt_serial_line="rts",
    )
    assert setup_problem(config, None) == f"RTS on {USB0} is already the repeater's PTT."
    no_device = dataclasses.replace(LINK_CONFIG, link_radio_cos="serial")
    assert "no serial port is chosen" in setup_problem(no_device, None)


def test_serial_ports_endpoint_and_device_validation():
    tmp = Path(tempfile.mkdtemp())
    ports = [{"path": "/dev/serial/by-id/usb-FTDI-if00-port0", "target": USB0}]
    client = TestClient(create_app(start_background_tick=False, log_path=tmp / "t.log", serial_ports=lambda: ports))

    assert client.get("/api/serial/ports").json() == ports
    ok = client.put("/api/config", json={"ptt_output": "serial", "ptt_serial_device": ports[0]["path"], "ptt_serial_line": "dtr"})
    assert ok.status_code == 200 and ok.json()["ptt_serial_line"] == "dtr"
    for bad in ("/etc/passwd", "/dev/sda", "/dev/serial/../sda x"):
        assert client.put("/api/config", json={"cos_serial_device": bad}).status_code == 422
    assert client.put("/api/config", json={"ptt_serial_line": "cts"}).status_code == 422
