import tempfile
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.live_audio import LiveAudio, cm108_from_env
from api.service import RepeaterService
from audio_io.engine import AudioEngine
from controller.state_machine import COURTESY_TONE, RECEIVING, RepeaterConfig
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


def test_dtmf_is_decoded_but_not_transmitted():
    import queue

    from controller.events import DTMFDigit
    from dsp.goertzel import dtmf_tone

    live, service, _clock, _engines = make_live()
    seen = []
    handle = service.handle_audio_events
    service.handle_audio_events = lambda events: (seen.extend(events), handle(events))
    engine = live.engine
    digit = dtmf_tone("5", RATE, int(0.2 * RATE)).astype(np.float32) * 0.5

    signal = np.concatenate([tone(level=0.3, blocks=10, hz=400.0), digit, tone(level=0.3, blocks=10, hz=400.0)])
    out = []
    for start in range(0, len(signal), BLOCK):
        engine.process_one(signal[start : start + BLOCK])
        while True:  # the output queue only holds a few blocks
            try:
                out.append(engine._output.get_nowait().reshape(-1))
            except queue.Empty:
                break
    out = np.concatenate(out)
    assert DTMFDigit(digit="5") in seen
    assert service.controller.state == RECEIVING and np.abs(out).max() > 0.1
    spectrum = np.abs(np.fft.rfft(out)) / len(out)
    freqs = np.fft.rfftfreq(len(out), 1 / RATE)
    assert spectrum[np.argmin(np.abs(freqs - 770))] < 0.002
    assert spectrum[np.argmin(np.abs(freqs - 1336))] < 0.002


def test_dtmf_mute_tail_and_tx_delay_apply_without_restart():
    live, service, _clock, engines = make_live()

    service.update_config(dtmf_mute=False, squelch_tail_ms=120.0, tx_delay_ms=60.0)

    settings = live.engine.processor.settings
    assert len(engines) == 1
    assert (settings.dtmf_mute, settings.squelch_tail_ms, settings.tx_delay_ms) == (False, 120.0, 60.0)


def test_simplex_node_hears_a_local_user_without_transmitting():
    live, service, _clock, engines = make_live(
        RepeaterConfig(callsign="W1AW", audio_enabled=True, vox_threshold_db=-30, vox_hold=0.1, node_mode="simplex")
    )
    engine = live.engine
    assert engine.processor.settings.local_repeat is False

    feed(engine, tone(blocks=5))
    assert service.controller.state == RECEIVING
    assert engine.processor.repeating_voice  # still sent to the links
    assert service.ptt_active is False and engine.transmitting is False

    service.update_config(node_mode="repeater")
    assert len(engines) == 1
    assert engine.processor.settings.local_repeat is True
    assert service.ptt_active is True


def test_each_receiver_opening_is_logged_and_listed_newest_first(caplog):
    live, service, clock, _engines = make_live()
    clock["now"] = 1000.0
    live._clock = lambda: clock["now"]

    with caplog.at_level("INFO", logger="moreopenrepeater.receiver"):
        feed(live.engine, tone(blocks=25, hz=1000.0))
        feed(live.engine, tone(level=0.0, blocks=10))
        feed(live.engine, tone(blocks=5, hz=700.0))
        feed(live.engine, tone(level=0.0, blocks=10))

    assert len(live.openings) == 2
    first = live.openings[0]
    assert first["strongest_hz"] == pytest.approx(1000, abs=10) and first["tone_share"] > 0.9
    assert first["started_at"] == pytest.approx(1000.0 - first["duration"])
    logged = [r.getMessage() for r in caplog.records if r.name == "moreopenrepeater.receiver"]
    assert "strongest 1000 Hz" in logged[0] and "while the repeater was transmitting" in logged[1]

    tmp = Path(tempfile.mkdtemp())
    client = TestClient(create_app(service=service, live_audio=live, start_background_tick=False, log_path=tmp / "t.log"))
    listed = client.get("/api/audio/openings").json()
    assert [round(o["strongest_hz"], -1) for o in listed] == [700, 1000]


def test_audio_glitches_are_logged_with_what_the_repeater_was_doing(caplog):
    live, service, clock, _engines = make_live()
    live._clock = lambda: clock["now"]
    engine, stream = live.engine, live.engine._stream

    with caplog.at_level("INFO", logger="moreopenrepeater.audio"):
        stream.starved_output_blocks = 1
        engine.check_glitches()
        clock["now"] = 0.5
        stream.starved_output_blocks = 3
        engine.check_glitches()  # the same glitch within a second: one entry
        service.simulate_cos(True)
        clock["now"] = 0.7
        stream.starved_output_blocks = 4
        engine.check_glitches()  # now transmitting: a new entry

    first, second = live.glitches
    assert (first["counter"], first["count"], first["transmitting"], first["state"]) == ("starved_output_blocks", 3, False, "idle")
    assert (second["count"], second["transmitting"], second["state"]) == (1, True, "receiving")
    logged = [r.getMessage() for r in caplog.records if r.getMessage().startswith("audio glitch")]
    assert logged == [
        "audio glitch: 1 transmit block with no audio ready in time, not transmitting (state idle)",
        "audio glitch: 1 transmit block with no audio ready in time, while transmitting (state receiving)",
    ]
    assert live.status()["starved_output_blocks"] == 4

    tmp = Path(tempfile.mkdtemp())
    client = TestClient(create_app(service=service, live_audio=live, start_background_tick=False, log_path=tmp / "t.log"))
    assert [g["state"] for g in client.get("/api/audio/glitches").json()] == ["receiving", "idle"]
    assert client.get("/api/audio/engine").json()["output_underflows"] == 0
