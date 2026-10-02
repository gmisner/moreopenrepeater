import tempfile
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.live_audio import LiveAudio, cm108_from_env
from api.service import RepeaterService
from audio_io.engine import AudioEngine
from controller.state_machine import COURTESY_TONE, IDLE, RECEIVING, RepeaterConfig
from playout.renderer import ClipRenderer

RATE = 16000
BLOCK = RATE // 50


class FakeStream:
    def __init__(self, sample_rate, block_size, input_queue, output_queue, device):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.device = device
        self.dropped_input_blocks = 0
        self.starved_output_blocks = 0

    def start(self):
        pass

    def stop(self):
        pass


class BrokenStream(FakeStream):
    def start(self):
        raise RuntimeError("Error querying device -1")


class ImmediateLoop:
    """Stands in for the asyncio loop: runs callbacks synchronously."""

    def call_soon_threadsafe(self, fn, *args):
        fn(*args)

    def run_in_executor(self, _executor, fn, *args):
        fn(*args)


class FakeCM108:
    def __init__(self):
        self.ptt = []
        self.cos = False
        self.cos_active_low = True

    def set_ptt(self, active):
        self.ptt.append(active)

    def read_cos(self):
        return self.cos


class FakePin:
    def __init__(self, pin, output, active_low):
        self.pin = pin
        self.output = output
        self.active_low = active_low
        self.active = False
        self.writes = []
        self.closed = False

    def read(self):
        return self.active

    def write(self, active):
        self.writes.append(active)

    def close(self):
        self.closed = True


class FakeHeader:
    def __init__(self, fail_pins=()):
        self.pins = {}
        self.fail_pins = fail_pins

    def open(self, pin, *, output, active_low):
        if pin in self.fail_pins:
            raise PermissionError(13, "Permission denied")
        self.pins[pin] = FakePin(pin, output, active_low)
        return self.pins[pin]


def make_live(config=None, stream=FakeStream, cm108=None, header=None):
    tmp = Path(tempfile.mkdtemp())
    renderer = ClipRenderer(AudioAssetStore(tmp / "audio").path_for, tts=None, sample_rate=RATE)
    clock = {"now": 0.0}
    service = RepeaterService(
        config=config or RepeaterConfig(callsign="W1AW", audio_enabled=True, vox_threshold_db=-30, vox_hold=0.1),
        clock=lambda: clock["now"],
        renderer=renderer,
    )
    engines = []

    def engine_factory(*args, **kwargs):
        engines.append(AudioEngine(*args, stream_factory=stream, **kwargs))
        return engines[-1]

    live = LiveAudio(
        service, renderer, cm108=cm108, engine_factory=engine_factory, open_pin=(header or FakeHeader()).open
    )
    live.attach(ImmediateLoop())
    return live, service, clock, engines


def tone(level=0.3, blocks=1, hz=1000.0):
    t = np.arange(BLOCK * blocks) / RATE
    return (level * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def feed(engine, signal):
    for start in range(0, len(signal), BLOCK):
        engine.process_one(signal[start : start + BLOCK])


def test_starts_engine_with_configured_devices_when_enabled():
    config = RepeaterConfig(audio_enabled=True, audio_input_device="BlackHole 2ch", audio_output_device="Speakers")
    live, _service, _clock, engines = make_live(config)

    assert live.engine is engines[0] and live.engine.running
    assert live.engine._stream.device == ("BlackHole 2ch", "Speakers")
    assert live.status()["running"] is True
    assert live.status()["sample_rate"] == RATE


def test_disabled_config_leaves_engine_off():
    live, _service, _clock, engines = make_live(RepeaterConfig(audio_enabled=False))
    assert engines == [] and live.engine is None
    assert live.status()["running"] is False


def test_carrier_on_input_drives_controller_and_repeats_audio():
    live, service, clock, _engines = make_live()
    engine = live.engine

    feed(engine, tone(blocks=5))
    assert service.cos_active is True
    assert service.controller.state == RECEIVING
    assert service.ptt_active is True
    assert engine.processor._repeating is True

    feed(engine, tone(level=0.0, blocks=10))
    assert service.cos_active is False
    assert service.controller.state == COURTESY_TONE
    assert engine.processor._repeating is False


def test_played_clip_is_queued_on_the_transmitter():
    live, service, _clock, _engines = make_live()
    service.simulate_cos(True)
    service.simulate_cos(False)
    assert service.last_clip == "courtesy_tone"
    assert live.engine.processor.playing


def test_level_settings_apply_live_without_restart():
    live, service, _clock, engines = make_live()
    service.update_config(vox_threshold_db=-50.0, tx_gain_db=6.0)
    assert len(engines) == 1
    assert live.engine.processor.settings.vox_threshold_db == -50.0
    assert live.engine.processor.settings.tx_gain_db == 6.0


def test_device_change_restarts_and_disable_stops():
    live, service, _clock, engines = make_live()
    service.update_config(audio_input_device="BlackHole 2ch")
    assert len(engines) == 2 and live.engine is engines[1]
    assert engines[0].running is False

    service.update_config(audio_enabled=False)
    assert live.engine is None and engines[1].running is False


def test_stopping_mid_transmission_releases_carrier():
    live, service, _clock, _engines = make_live()
    feed(live.engine, tone(blocks=5))
    assert service.controller.state == RECEIVING

    service.update_config(audio_enabled=False)
    assert service.cos_active is False
    assert service.controller.state != RECEIVING


def test_device_open_failure_is_reported_not_raised():
    live, _service, _clock, _engines = make_live(stream=BrokenStream)
    assert live.engine is None
    assert "Error querying device" in live.status()["error"]


def test_cm108_cos_source_requires_interface():
    live, _service, _clock, engines = make_live(RepeaterConfig(audio_enabled=True, cos_source="cm108"))
    assert engines == []
    assert "CM108" in live.status()["error"]


def test_cm108_supplies_cos_and_hardware_ptt():
    cm108 = FakeCM108()
    live, service, _clock, _engines = make_live(RepeaterConfig(audio_enabled=True, cos_source="cm108"), cm108=cm108)
    engine = live.engine
    assert engine.processor.settings.cos_source == "external"
    assert live.status()["hardware_ptt"] == "cm108"

    engine.processor.set_external_cos(True)  # what the COS poll thread does
    feed(engine, tone(level=0.0, blocks=1))
    assert service.controller.state == RECEIVING
    feed(engine, tone(level=0.0, blocks=1))  # PTT keys on the first block transmitted after the controller asks
    assert cm108.ptt == [True]


def test_cm108_is_found_automatically_unless_turned_off(tmp_path):
    node = tmp_path / "hidraw3"
    node.touch()
    found = [{"path": str(node), "vendor": 0x0D8C, "product": 0x0012, "name": "USB Audio Device"}]

    assert cm108_from_env({}, find=lambda: found) is not None
    assert cm108_from_env({"MOREOPENREPEATER_CM108_HIDRAW": "auto"}, find=lambda: found) is not None
    assert cm108_from_env({}, find=lambda: []) is None
    assert cm108_from_env({"MOREOPENREPEATER_CM108_HIDRAW": "off"}, find=lambda: found) is None


def test_cm108_is_unkeyed_when_found(tmp_path):
    """A CM108 keeps PTT keyed after a crash; the next start unkeys it."""
    node = tmp_path / "hidraw3"
    node.touch()
    cm108_from_env({"MOREOPENREPEATER_CM108_HIDRAW": str(node)}, find=lambda: [])
    assert node.read_bytes() == bytes([0x00, 0x00, 0x00, 0x04, 0x00])  # GPIO3 an output, driven low


def test_release_ptt_and_progress_for_the_watchdog():
    cm108 = FakeCM108()
    live, _service, _clock, _engines = make_live(cm108=cm108)
    assert live.progress() is not None
    live.release_ptt()
    assert cm108.ptt == [False]

    live.shutdown()
    assert live.progress() is None
    live.release_ptt()  # nothing to release once the engine is stopped
    assert cm108.ptt == [False]


def test_cm108_path_can_be_set_explicitly(tmp_path):
    node = tmp_path / "hidraw7"
    node.touch()
    assert cm108_from_env({"MOREOPENREPEATER_CM108_HIDRAW": str(node)}, find=lambda: []) is not None


def test_cos_polarity_applies_to_the_cm108():
    cm108 = FakeCM108()
    _live, service, _clock, engines = make_live(RepeaterConfig(audio_enabled=True, cos_source="cm108"), cm108=cm108)
    assert cm108.cos_active_low is True

    service.update_config(cos_polarity="high")
    assert len(engines) == 2
    assert cm108.cos_active_low is False


def test_pi_header_pins_supply_cos_and_ptt():
    header = FakeHeader()
    config = RepeaterConfig(
        audio_enabled=True, cos_source="gpio", cos_gpio_pin=27, cos_polarity="high", ptt_output="gpio", ptt_gpio_pin=17
    )
    cm108 = FakeCM108()
    live, service, _clock, _engines = make_live(config, cm108=cm108, header=header)
    engine = live.engine
    ptt, cos = header.pins[17], header.pins[27]
    assert (ptt.output, ptt.active_low) == (True, False)
    assert (cos.output, cos.active_low) == (False, False)
    assert engine.processor.settings.cos_source == "external"
    assert live.status()["hardware_ptt"] == "gpio"

    cos.active = True
    engine.processor.set_external_cos(engine._cos_input())  # one step of the COS poll thread
    feed(engine, tone(level=0.0, blocks=2))
    assert service.controller.state == RECEIVING
    assert ptt.writes == [True]
    assert cm108.ptt == []  # the CM108 no longer keys the radio

    service.update_config(audio_enabled=False)
    assert ptt.writes[-1] is False
    assert ptt.closed and cos.closed


def test_ptt_and_cos_cannot_share_a_pin():
    config = RepeaterConfig(audio_enabled=True, cos_source="gpio", ptt_output="gpio", cos_gpio_pin=17, ptt_gpio_pin=17)
    live, _service, _clock, engines = make_live(config)
    assert engines == []
    assert "GPIO17" in live.status()["error"]


def test_pin_open_failure_is_reported_and_releases_other_pins():
    header = FakeHeader(fail_pins=(27,))
    config = RepeaterConfig(audio_enabled=True, cos_source="gpio", ptt_output="gpio")
    live, _service, _clock, engines = make_live(config, header=header)
    assert engines == []
    assert live.status()["error"] == "couldn't open GPIO27 for COS: Permission denied"
    assert header.pins[17].closed


def test_audio_endpoints():
    tmp = Path(tempfile.mkdtemp())
    devices = [{"name": "BlackHole 2ch", "inputs": 2, "outputs": 2, "default_samplerate": 48000.0}]
    app = create_app(start_background_tick=False, log_path=tmp / "t.log", audio_devices=lambda: devices)
    client = TestClient(app)

    assert client.get("/api/audio/devices").json() == devices
    engine = client.get("/api/audio/engine").json()
    assert engine["enabled"] is False and engine["running"] is False
    assert engine["rx_level_db"] <= -100


def test_audio_devices_endpoint_reports_portaudio_errors():
    tmp = Path(tempfile.mkdtemp())

    def broken():
        raise OSError("PortAudio not initialized")

    client = TestClient(create_app(start_background_tick=False, log_path=tmp / "t.log", audio_devices=broken))
    assert client.get("/api/audio/devices").status_code == 503
