import dataclasses
import queue
import tempfile
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.link_radio import LinkRadio, setup_problem
from api.live_audio import LiveAudio, link_cm108_from_env
from api.service import RepeaterService
from audio_io.engine import AudioEngine
from controller.events import DTMFDigit
from controller.macros import Macro
from controller.state_machine import COURTESY_TONE, IDLE, RECEIVING, RepeaterConfig
from playout.renderer import ClipRenderer

from test_live_audio import FakeCM108, FakeHeader, FakeStream, ImmediateLoop

RATE = 16000
BLOCK = RATE // 50

CONFIG = RepeaterConfig(
    callsign="W1AW",
    audio_enabled=True,
    audio_input_device="Repeater In",
    audio_output_device="Repeater Out",
    vox_threshold_db=-30,
    vox_hold=0.1,
    link_radio_enabled=True,
    link_radio_input_device="Link In",
    link_radio_output_device="Link Out",
    link_radio_vox_threshold_db=-30,
    link_radio_courtesy_tone=False,
)


class Rig:
    def __init__(self, config=CONFIG, header=None, cm108=None):
        tmp = Path(tempfile.mkdtemp())
        self.renderer = ClipRenderer(AudioAssetStore(tmp / "audio").path_for, tts=None, sample_rate=RATE)
        self.clock = {"now": 0.0}
        self.service = RepeaterService(config=config, clock=lambda: self.clock["now"], renderer=self.renderer)
        self.header = header or FakeHeader()
        self.engines = []

        def engine_factory(*args, **kwargs):
            self.engines.append(AudioEngine(*args, stream_factory=FakeStream, **kwargs))
            return self.engines[-1]

        self.live = LiveAudio(self.service, self.renderer, engine_factory=engine_factory, open_pin=self.header.open)
        self.live.attach(ImmediateLoop())
        self.link = LinkRadio(
            self.service,
            self.renderer,
            sending=lambda: self.live.repeating_voice,
            attach_port=self.live.set_port,
            find_cm108=lambda: cm108,
            engine_factory=engine_factory,
            open_pin=self.header.open,
            clock=lambda: self.clock["now"],
        )
        self.link.attach(ImmediateLoop())

    def step(self, engine, signal):
        """Feed `signal` block by block, moving audio across and updating the link transmitter."""
        out = []
        for start in range(0, len(signal), BLOCK):
            engine.process_one(signal[start : start + BLOCK])
            self.link.pump()
            self.clock["now"] += BLOCK / RATE
            self.link.update(self.clock["now"])
            out.append(drain(engine))
        return np.concatenate(out)


def drain(engine):
    blocks = []
    while True:
        try:
            blocks.append(engine._output.get_nowait().reshape(-1))
        except queue.Empty:
            return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)


def tone(level=0.3, blocks=1, hz=1000.0):
    t = np.arange(BLOCK * blocks) / RATE
    return (level * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def silence(blocks):
    return np.zeros(BLOCK * blocks, dtype=np.float32)


def test_starts_its_own_engine_on_its_own_devices():
    rig = Rig()

    assert rig.link.engine is not None and rig.link.engine.running
    assert rig.link.engine._stream.device == ("Link In", "Link Out")
    assert rig.live.engine._stream.device == ("Repeater In", "Repeater Out")
    assert rig.live.engine.processor._port is not None
    assert rig.link.status()["running"] is True


def test_far_end_keys_the_repeater_and_is_transmitted_with_the_link_courtesy_tone():
    rig = Rig()
    link_engine, repeater = rig.link.engine, rig.live.engine
    drain(repeater)

    rig.step(link_engine, tone(blocks=10, hz=700.0))

    assert rig.link.receiving
    assert rig.service.controller.state == RECEIVING
    assert rig.service.ptt_active
    out = rig.step(repeater, silence(10))
    assert np.abs(out).max() > 0.1  # the far end's audio, out the repeater's transmitter

    rig.step(link_engine, silence(30))

    assert not rig.link.receiving
    assert rig.service.controller.state == COURTESY_TONE
    assert rig.service.last_clip == "courtesy_tone_link"


def test_local_user_is_sent_out_the_link_radio_and_it_unkeys_after():
    rig = Rig()
    link_engine, repeater = rig.link.engine, rig.live.engine
    drain(link_engine)

    rig.step(repeater, tone(blocks=10))

    assert rig.service.controller.state == RECEIVING
    assert rig.link.controller.ptt and rig.link.engine.processor._ptt
    out = rig.step(link_engine, silence(10))
    assert np.abs(out).max() > 0.1
    assert link_engine.transmitting

    rig.step(repeater, silence(60))
    assert not rig.link.controller.ptt
    rig.step(link_engine, silence(5))
    assert not link_engine.transmitting


def test_the_far_end_is_not_sent_back_out_the_link():
    rig = Rig()

    rig.step(rig.link.engine, tone(blocks=10, hz=700.0))
    rig.step(rig.live.engine, silence(10))

    assert rig.service.controller.state == RECEIVING
    assert not rig.live.repeating_voice
    assert not rig.link.controller.ptt


def test_link_ptt_and_cos_on_header_pins():
    config = dataclasses.replace(CONFIG, link_radio_cos="gpio", link_radio_cos_polarity="high", link_radio_ptt="gpio")
    rig = Rig(config)

    cos, ptt = rig.header.pins[23], rig.header.pins[24]
    assert not cos.output and not cos.active_low
    assert ptt.output and not ptt.active_low

    cos.active = True
    rig.link.engine.processor.set_external_cos(True)  # what the engine's COS poller does with cos.read()
    rig.step(rig.link.engine, silence(2))
    assert rig.link.receiving

    rig.service.update_config(link_radio_enabled=False)
    assert cos.closed and ptt.closed
    assert rig.link.engine is None
    assert not rig.link.receiving
    assert rig.service.controller.state in (COURTESY_TONE, IDLE)


def test_second_cm108_keys_the_link_radio():
    cm108 = FakeCM108()
    rig = Rig(dataclasses.replace(CONFIG, link_radio_ptt="cm108"), cm108=cm108)

    rig.step(rig.live.engine, tone(blocks=10))
    rig.step(rig.link.engine, silence(2))

    assert cm108.ptt[-1] is True


def test_settings_it_refuses():
    assert "live audio" in setup_problem(dataclasses.replace(CONFIG, audio_enabled=False), None)
    assert "own input" in setup_problem(dataclasses.replace(CONFIG, link_radio_input_device="Repeater In"), None)
    assert "own output" in setup_problem(dataclasses.replace(CONFIG, link_radio_output_device="Repeater Out"), None)
    monitor = dataclasses.replace(CONFIG, monitor_enabled=True, monitor_input_device="Link In")
    assert "monitor" in setup_problem(monitor, None)
    pins = dataclasses.replace(CONFIG, ptt_output="gpio", ptt_gpio_pin=24, link_radio_ptt="gpio", link_radio_ptt_gpio_pin=24)
    assert "repeater's PTT" in setup_problem(pins, None)
    same = dataclasses.replace(
        CONFIG, link_radio_cos="gpio", link_radio_ptt="gpio", link_radio_cos_gpio_pin=5, link_radio_ptt_gpio_pin=5
    )
    assert "both use GPIO5" in setup_problem(same, None)
    assert "second CM108" in setup_problem(dataclasses.replace(CONFIG, link_radio_ptt="cm108"), None)
    assert setup_problem(CONFIG, None) is None


def test_a_problem_shows_as_the_error_and_nothing_starts():
    rig = Rig(dataclasses.replace(CONFIG, link_radio_input_device="Repeater In"))

    assert rig.link.engine is None
    assert "own input" in rig.link.status()["error"]
    assert rig.live.engine.processor._port is None


def test_level_settings_apply_without_a_restart():
    rig = Rig()
    engine = rig.link.engine

    rig.service.update_config(link_radio_vox_threshold_db=-45.0, link_radio_tx_gain_db=-3.0, link_radio_tx_ctcss_hz=100.0)

    assert rig.link.engine is engine
    assert engine.processor.settings.vox_threshold_db == -45.0
    assert engine.processor.settings.tx_ctcss_hz == 100.0


def test_repeater_restart_keeps_the_link_attached():
    rig = Rig()

    rig.service.update_config(audio_output_device="Other Out")

    assert rig.live.engine.processor._port is not None


def test_dtmf_macros_turn_the_link_radio_on_and_off():
    rig = Rig()
    rig.service.add_macro(Macro(pattern="*50", description="", action="link_radio_off"))
    rig.service.add_macro(Macro(pattern="*51", description="", action="link_radio_on"))

    for digit in "*50":
        rig.service.handle_audio_events([DTMFDigit(digit=digit)])
    rig.clock["now"] += 5
    rig.service.tick()
    assert not rig.service.config.link_radio_enabled
    assert rig.link.engine is None

    for digit in "*51":
        rig.service.handle_audio_events([DTMFDigit(digit=digit)])
    rig.clock["now"] += 5
    rig.service.tick()
    assert rig.service.config.link_radio_enabled
    assert rig.link.engine is not None


def test_link_cm108_skips_the_repeaters_interface(monkeypatch):
    opened = []

    class FakeDevice:
        def __init__(self, path):
            self.path = path
            opened.append(path)

        def write_report(self, report):
            pass

    monkeypatch.setattr("api.live_audio.LinuxHidrawDevice", FakeDevice)
    found = [{"path": "/dev/hidraw0", "name": "a"}, {"path": "/dev/hidraw1", "name": "b"}]

    class Repeater:
        device = FakeDevice("/dev/hidraw0")

    opened.clear()
    interface = link_cm108_from_env({}, Repeater(), find=lambda: found)
    assert interface.device.path == "/dev/hidraw1"
    assert link_cm108_from_env({}, None, find=lambda: found).device.path == "/dev/hidraw0"
    assert link_cm108_from_env({}, Repeater(), find=lambda: found[:1]) is None
    assert link_cm108_from_env({"MOREOPENREPEATER_LINK_CM108_HIDRAW": "off"}, None, find=lambda: found) is None
    assert link_cm108_from_env({"MOREOPENREPEATER_LINK_CM108_HIDRAW": "/dev/hidraw7"}, None).device.path == "/dev/hidraw7"


def test_status_endpoint_and_clearing_the_tone():
    client = TestClient(create_app())

    status = client.get("/api/link-radio").json()
    assert status["enabled"] is False and status["running"] is False

    assert client.put("/api/config", json={"link_radio_tx_ctcss_hz": 100.0}).json()["link_radio_tx_ctcss_hz"] == 100.0
    assert client.put("/api/config", json={"clear_link_radio_tx_ctcss_hz": True}).json()["link_radio_tx_ctcss_hz"] is None


def test_a_radio_keyed_by_its_own_vox_needs_no_ptt_line():
    rig = Rig(dataclasses.replace(CONFIG, link_radio_ptt="none"))

    assert rig.link.engine is not None and rig.link.engine._ptt_output is None
    assert rig.header.pins == {}
