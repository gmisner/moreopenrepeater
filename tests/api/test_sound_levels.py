import subprocess
import tempfile
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.live_audio import LiveAudio
from api.mixer import Volume, apply_saved_levels, parse_volumes, read_volumes, side_volumes
from api.service import RepeaterService
from audio_io.engine import AudioEngine
from controller.state_machine import RepeaterConfig
from playout.renderer import ClipRenderer

RATE = 8000
CM108 = "USB Audio Device: - (hw:2,0)"

# `amixer -c 2 scontents` on the Pi's CM108 interface.
PI_CM108 = """\
Simple mixer control 'Speaker',0
  Capabilities: pvolume pswitch pswitch-joined
  Playback channels: Front Left - Front Right
  Limits: Playback 0 - 37
  Mono:
  Front Left: Playback 14 [38%] [-23.00dB] [on]
  Front Right: Playback 14 [38%] [-23.00dB] [on]
Simple mixer control 'Mic',0
  Capabilities: pvolume pvolume-joined cvolume cvolume-joined pswitch pswitch-joined cswitch cswitch-joined
  Playback channels: Mono
  Capture channels: Mono
  Limits: Playback 0 - 31 Capture 0 - 35
  Mono: Playback 16 [52%] [-7.00dB] [off] Capture 30 [86%] [18.00dB] [on]
Simple mixer control 'Auto Gain Control',0
  Capabilities: pswitch pswitch-joined
  Playback channels: Mono
  Mono: Playback [on]
"""


class FakeCard:
    """amixer for one CM108-like card: `scontents`, and `sset` by step,
    percent or dB, in one direction or (like amixer) both."""

    # control -> direction -> [min, max, value, dB at step 0]
    def __init__(self, card=2):
        self.card = card
        self.volumes = {
            "Speaker": {"playback": [0, 37, 14, -37]},
            "Mic": {"playback": [0, 31, 16, -23], "capture": [0, 35, 30, -12]},
        }
        self.sets = []

    def __call__(self, args):
        if args[args.index("-c") + 1] != str(self.card):
            return subprocess.CompletedProcess(args, 1, "", "amixer: Invalid card number\n")
        if args[-1] == "scontents":
            return subprocess.CompletedProcess(args, 0, self.scontents(), "")
        control, *rest = args[args.index("sset") + 1:]
        if control not in self.volumes:
            return subprocess.CompletedProcess(args, 1, "", f"amixer: Unable to find simple control '{control}',0\n")
        directions = [rest.pop(0)] if rest[0] in ("playback", "capture") else list(self.volumes[control])
        for direction in directions:
            low, high, _, offset = volume = self.volumes[control][direction]
            amount = rest[0]
            if amount.endswith("%"):
                step = round(int(amount[:-1]) * high / 100)
            elif amount.endswith("dB"):
                step = round(float(amount[:-2]) - offset)
            else:
                step = int(amount)
            volume[2] = min(max(step, low), high)
        self.sets.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    def value(self, control, direction):
        return self.volumes[control][direction][2]

    def reading(self, control, direction):
        _, high, value, offset = self.volumes[control][direction]
        return f"{direction.title()} {value} [{round(100 * value / high)}%] [{value + offset:.2f}dB]"

    def scontents(self):
        speaker = self.reading("Speaker", "playback")
        return (
            PI_CM108
            .replace("Front Left: Playback 14 [38%] [-23.00dB]", f"Front Left: {speaker}")
            .replace("Front Right: Playback 14 [38%] [-23.00dB]", f"Front Right: {speaker}")
            .replace("Playback 16 [52%] [-7.00dB]", self.reading("Mic", "playback"))
            .replace("Capture 30 [86%] [18.00dB]", self.reading("Mic", "capture"))
        )


class FakeTTS:
    name = "fake"

    def synthesize(self, text, voice=""):
        return np.full(RATE, 0.1, dtype=np.float32), RATE


def make_client(config=None, amixer=None):
    tmp = Path(tempfile.mkdtemp())
    assets = AudioAssetStore(tmp / "audio")
    renderer = ClipRenderer(assets.path_for, tts=FakeTTS(), sample_rate=RATE)
    config = config or RepeaterConfig(callsign="W1AW", audio_input_device=CM108, audio_output_device=CM108)
    service = RepeaterService(config=config, clock=lambda: 0.0, renderer=renderer)
    amixer = amixer or FakeCard()
    app = create_app(
        service=service, start_background_tick=False, assets_store=assets, log_path=tmp / "t.log", renderer=renderer, run_mixer=amixer
    )
    return TestClient(app), service, amixer


def test_parses_each_control_and_direction_from_scontents():
    assert parse_volumes(PI_CM108) == [
        Volume("Speaker", "playback", 0, 37, 14, -23.0),
        Volume("Mic", "playback", 0, 31, 16, -7.0),
        Volume("Mic", "capture", 0, 35, 30, 18.0),
    ]


def test_parses_a_volume_without_db():
    text = "Simple mixer control 'PCM',0\n  Limits: Playback 0 - 255\n  Mono: Playback 200 [78%]\n"
    assert parse_volumes(text) == [Volume("PCM", "playback", 0, 255, 200, None)]


def test_input_side_is_capture_and_output_side_leaves_out_the_mic_monitor():
    volumes = parse_volumes(PI_CM108)
    assert [(v.control, v.direction) for v in side_volumes("input", volumes)] == [("Mic", "capture")]
    assert [(v.control, v.direction) for v in side_volumes("output", volumes)] == [("Speaker", "playback")]


def test_read_volumes_reports_a_missing_amixer():
    def no_amixer(args):
        raise FileNotFoundError(args[0])

    try:
        read_volumes(0, no_amixer)
    except OSError as error:
        assert "alsa-utils" in str(error)
    else:
        raise AssertionError("expected OSError")


def test_saved_levels_are_set_by_direction_on_each_sides_card():
    card = FakeCard()
    config = RepeaterConfig(
        audio_input_device=CM108, audio_output_device=CM108,
        audio_input_levels={"Mic": 22}, audio_output_levels={"Speaker": 30},
    )

    apply_saved_levels(config, card)

    assert [s[4:] for s in card.sets] == [["sset", "Mic", "capture", "22"], ["sset", "Speaker", "playback", "30"]]
    assert card.value("Mic", "playback") == 16  # the monitor path is left alone


def test_saved_levels_skip_the_system_default_device():
    card = FakeCard()
    apply_saved_levels(RepeaterConfig(audio_input_levels={"Mic": 22}), card)
    assert card.sets == []


def test_levels_route_lists_each_sides_volumes_and_whether_theyre_saved():
    client, service, _ = make_client()
    service.update_config(audio_output_levels={"Speaker": 14})

    body = client.get("/api/audio/levels").json()

    assert body["notes"] == []
    assert body["levels"] == [
        {"side": "input", "card": 2, "control": "Mic", "min": 0, "max": 35, "value": 30, "db": 18.0, "saved": False},
        {"side": "output", "card": 2, "control": "Speaker", "min": 0, "max": 37, "value": 14, "db": -23.0, "saved": True},
    ]


def test_setting_a_level_changes_the_card_and_saves_it():
    client, service, card = make_client()

    response = client.put("/api/audio/levels", json={"side": "input", "control": "Mic", "value": 25})

    assert response.status_code == 200
    assert card.value("Mic", "capture") == 25 and card.value("Mic", "playback") == 16
    assert service.saved_config.audio_input_levels == {"Mic": 25}
    mic = next(level for level in response.json()["levels"] if level["control"] == "Mic")
    assert (mic["value"], mic["db"], mic["saved"]) == (25, 13.0, True)


def test_a_level_past_the_controls_range_is_clamped():
    client, service, card = make_client()
    client.put("/api/audio/levels", json={"side": "output", "control": "Speaker", "value": 99})
    assert card.value("Speaker", "playback") == 37
    assert service.saved_config.audio_output_levels == {"Speaker": 37}


def test_an_unknown_control_or_the_wrong_side_is_refused():
    client, service, _ = make_client()
    assert client.put("/api/audio/levels", json={"side": "input", "control": "Speaker", "value": 5}).status_code == 404
    assert client.put("/api/audio/levels", json={"side": "input", "control": "Treble", "value": 5}).status_code == 404
    assert service.saved_config.audio_input_levels == {}


def test_levels_need_a_specific_sound_card():
    client, *_ = make_client(config=RepeaterConfig(callsign="W1AW"))
    assert client.get("/api/audio/levels").json()["levels"] == []
    assert len(client.get("/api/audio/levels").json()["notes"]) == 2
    assert client.put("/api/audio/levels", json={"side": "input", "control": "Mic", "value": 5}).status_code == 409


def test_a_card_amixer_cant_read_is_a_note_not_an_error():
    other = "Other (hw:5,0)"
    client, *_ = make_client(config=RepeaterConfig(callsign="W1AW", audio_input_device=other, audio_output_device=other))
    body = client.get("/api/audio/levels").json()
    assert body["levels"] == [] and "Invalid card number" in body["notes"][0]


def test_a_board_preset_saves_the_levels_it_set():
    client, service, card = make_client(config=RepeaterConfig(callsign="W1AW"))

    client.post("/api/boards/dmk-uri/apply", json={"input_device": CM108, "output_device": CM108})

    config = service.saved_config
    assert config.audio_input_levels == {"Mic": 30} == {"Mic": card.value("Mic", "capture")}
    assert config.audio_output_levels == {"Speaker": card.value("Speaker", "playback")} == {"Speaker": 28}


class FakeStream:
    def __init__(self, sample_rate, block_size, input_queue, output_queue, device):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.device = device
        self.dropped_input_blocks = 0
        self.starved_output_blocks = 0

    def start(self):
        events.append("stream started")

    def stop(self):
        pass


events: list[str] = []


def test_the_engine_sets_the_saved_levels_before_opening_the_card():
    events.clear()
    card = FakeCard()
    tmp = Path(tempfile.mkdtemp())
    renderer = ClipRenderer(AudioAssetStore(tmp / "audio").path_for, tts=None, sample_rate=16000)
    config = RepeaterConfig(
        callsign="W1AW", audio_enabled=True, audio_input_device=CM108, audio_output_device=CM108,
        audio_input_levels={"Mic": 20},
    )
    service = RepeaterService(config=config, clock=lambda: 0.0, renderer=renderer)

    def amixer(args):
        events.append(" ".join(args[4:]))
        return card(args)

    live = LiveAudio(
        service, renderer,
        engine_factory=lambda *a, **k: AudioEngine(*a, stream_factory=FakeStream, **k),
        run_mixer=amixer,
    )
    live.apply_config(service.config)

    assert events[:2] == ["sset Mic capture 20", "stream started"]
    assert card.value("Mic", "capture") == 20
    live.shutdown()
