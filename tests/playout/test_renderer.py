import dataclasses
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from controller.state_machine import RepeaterConfig
from playout.renderer import ClipRenderer, UnknownClipError
from playout.tts import TTSError
from playout.wav import encode_wav

RATE = 8000


class FakeTTS:
    name = "fake"

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def synthesize(self, text, voice=""):
        self.calls.append((text, voice))
        if self.fail:
            raise TTSError("boom")
        return np.full(RATE, 0.1, dtype=np.float32), RATE  # 1 second of "speech"


def make_renderer(tts=None, assets=None, wall_clock=datetime.now):
    tmp = Path(tempfile.mkdtemp())
    for asset_id, samples in (assets or {}).items():
        (tmp / f"{asset_id}.wav").write_bytes(encode_wav(samples, RATE))
    return ClipRenderer(lambda asset_id: tmp / f"{asset_id}.wav", tts=tts, sample_rate=RATE, wall_clock=wall_clock), tmp


def config(**overrides):
    return dataclasses.replace(RepeaterConfig(callsign="W1AW"), **overrides)


def test_builtin_courtesy_tone_uses_style_and_duration():
    renderer, _ = make_renderer()
    samples = renderer.render("courtesy_tone", config(courtesy_tone_duration=0.25, courtesy_tone_style="chirp"))
    assert abs(len(samples) - 2000) <= 2


def test_cw_and_custom_courtesy_tones_follow_their_settings():
    renderer, _ = make_renderer()
    slow = renderer.render("courtesy_tone", config(courtesy_tone_style="cw_k", cw_wpm=10))
    fast = renderer.render("courtesy_tone", config(courtesy_tone_style="cw_k", cw_wpm=20))
    assert len(slow) > len(fast)
    custom = config(courtesy_tone_style="custom", courtesy_tone_custom="1000:250")
    assert len(renderer.render("courtesy_tone", custom)) == 2000
    assert renderer.cached_duration("courtesy_tone", custom) == 0.25
    assert len(renderer.render("courtesy_tone", dataclasses.replace(custom, courtesy_tone_custom="1000:100"))) == 800


def test_assigned_courtesy_asset_replaces_the_builtin():
    clip = np.full(123, 0.2, dtype=np.float32)
    renderer, _ = make_renderer(assets={"abc": clip})
    samples = renderer.render("courtesy_tone", config(courtesy_tone_asset_id="abc"))
    assert len(samples) == 123


def test_missing_asset_falls_back_to_builtin_tone():
    renderer, _ = make_renderer()
    samples = renderer.render("timeout_tone", config(timeout_tone_asset_id="deleted"))
    assert len(samples) == int(0.75 * RATE)


def test_cw_id_spells_the_callsign():
    renderer, _ = make_renderer()
    samples = renderer.render("id", config(id_mode="cw", cw_wpm=20))
    # W(9) + gap(3) + 1(17) + gap(3) + A(5) + gap(3) + W(9) = 49 units of 60ms at 20 WPM.
    assert abs(len(samples) / RATE - 49 * 0.06) < 0.01


def test_voice_id_uses_tts_with_the_formatted_text():
    tts = FakeTTS()
    renderer, _ = make_renderer(tts=tts)
    samples = renderer.render("id", config(id_mode="voice", voice_id_text="This is {callsign}", tts_voice="Alex"))
    assert tts.calls == [("This is W 1 A W", "Alex")]
    assert len(samples) == RATE
    assert np.isclose(np.max(np.abs(samples)), 0.5)  # speech normalized to a consistent level


def test_voice_id_prefers_an_uploaded_clip_over_tts():
    tts = FakeTTS()
    renderer, _ = make_renderer(tts=tts, assets={"myid": np.full(50, 0.3, dtype=np.float32)})
    samples = renderer.render("id", config(id_mode="voice", id_asset_id="myid"))
    assert len(samples) == 50
    assert tts.calls == []


def test_voice_id_falls_back_to_cw_without_tts():
    renderer, _ = make_renderer(tts=None)
    voice = renderer.render("id", config(id_mode="voice"))
    cw = renderer.render("id", config(id_mode="cw"))
    assert len(voice) == len(cw) > 0


def test_voice_id_falls_back_to_cw_when_tts_fails():
    renderer, _ = make_renderer(tts=FakeTTS(fail=True))
    assert len(renderer.render("id", config(id_mode="voice"))) > 0


def test_both_mode_is_voice_then_gap_then_cw():
    renderer, _ = make_renderer(tts=FakeTTS())
    cw_len = len(renderer.render("id", config(id_mode="cw")))
    both = renderer.render("id", config(id_mode="both"))
    assert len(both) == RATE + int(0.4 * RATE) + cw_len


def test_empty_callsign_renders_silence_not_an_error():
    renderer, _ = make_renderer()
    assert len(renderer.render("id", config(callsign="", id_mode="cw"))) == 0


def test_tts_clip_speaks_arbitrary_text():
    tts = FakeTTS()
    renderer, _ = make_renderer(tts=tts)
    renderer.render("tts:Net tonight at 8", config())
    assert tts.calls[0][0] == "Net tonight at 8"


def test_tts_clip_spells_out_the_callsign_placeholder():
    tts = FakeTTS()
    renderer, _ = make_renderer(tts=tts)
    renderer.render("tts:This is {callsign}", config(callsign="W1AW", id_phonetic=True))
    assert tts.calls[0][0] == "This is Whiskey one Alpha Whiskey"


def test_asset_clip_plays_an_uploaded_file():
    renderer, _ = make_renderer(assets={"xyz": np.zeros(77, dtype=np.float32)})
    assert len(renderer.render("asset:xyz", config())) == 77


@pytest.mark.parametrize("clip", ["nonsense", "asset:missing"])
def test_unknown_clips_raise(clip):
    renderer, _ = make_renderer()
    with pytest.raises(UnknownClipError):
        renderer.render(clip, config())


def test_results_are_cached_until_relevant_config_changes():
    tts = FakeTTS()
    renderer, _ = make_renderer(tts=tts)
    renderer.render("id", config(id_mode="voice"))
    renderer.render("id", config(id_mode="voice", hang_time=9.0))  # irrelevant field
    assert len(tts.calls) == 1
    renderer.render("id", config(id_mode="voice", callsign="K1ABC"))
    assert len(tts.calls) == 2


def test_cached_duration_never_renders():
    tts = FakeTTS()
    renderer, _ = make_renderer(tts=tts)
    assert renderer.cached_duration("id", config(id_mode="voice")) is None
    assert tts.calls == []
    renderer.warm(config(id_mode="voice"))
    assert renderer.cached_duration("id", config(id_mode="voice")) == 1.0


def test_cw_suffix_follows_the_callsign():
    renderer, _ = make_renderer()
    plain = renderer.render("id", config(id_mode="cw", cw_wpm=20))
    suffixed = renderer.render("id", config(id_mode="cw", cw_wpm=20, cw_id_suffix="/R"))
    # gap(3) + /(13) + gap(3) + R(7) = 26 more units of 60ms at 20 WPM.
    assert abs((len(suffixed) - len(plain)) / RATE - 26 * 0.06) < 0.01


def long_id_renderer(tts, when, assets=None):
    clock = [when]
    renderer, _ = make_renderer(tts=tts, assets=assets, wall_clock=lambda: clock[0])
    return renderer, clock


def test_long_id_says_the_time_and_spells_the_callsign():
    tts = FakeTTS()
    renderer, _ = long_id_renderer(tts, datetime(2026, 10, 2, 14, 5))
    renderer.render("id_long", config(long_id_mode="voice", long_id_text="This is {callsign}. The time is {time}."))
    assert tts.calls == [("This is W 1 A W. The time is 2 oh 5 P M.", "")]


def test_long_id_both_adds_cw_with_the_suffix():
    renderer, _ = long_id_renderer(FakeTTS(), datetime(2026, 10, 2, 9, 0))
    cw = renderer.render("id", config(id_mode="cw", cw_id_suffix="/R"))
    both = renderer.render("id_long", config(long_id_mode="both", cw_id_suffix="/R"))
    assert len(both) == RATE + int(0.4 * RATE) + len(cw)


def test_long_id_prefers_its_uploaded_clip():
    tts = FakeTTS()
    renderer, _ = long_id_renderer(tts, datetime(2026, 10, 2, 9, 0), assets={"long": np.full(60, 0.3, dtype=np.float32)})
    assert len(renderer.render("id_long", config(long_id_mode="voice", long_id_asset_id="long"))) == 60
    assert tts.calls == []


def test_long_id_is_cached_per_minute_and_warmed_a_minute_ahead():
    tts = FakeTTS()
    renderer, clock = long_id_renderer(tts, datetime(2026, 10, 2, 9, 0, 30))
    cfg = config(long_id_mode="voice")
    renderer.warm_long_id(cfg)
    assert [text for text, _ in tts.calls] == ["This is W 1 A W. The time is 9 o'clock A M.", "This is W 1 A W. The time is 9 oh 1 A M."]
    clock[0] = datetime(2026, 10, 2, 9, 1, 10)
    assert renderer.cached_duration("id_long", cfg) == 1.0  # ready before anyone asks
    renderer.warm_long_id(cfg)
    assert len(tts.calls) == 3
    assert len([key for key in renderer._cache if key[0] == "id_long"]) == 2  # the 9:00 one is gone


def test_long_id_isnt_rendered_while_off():
    tts = FakeTTS()
    renderer, _ = make_renderer(tts=tts)
    renderer.warm(config(id_mode="cw"))
    assert tts.calls == []


def test_courtesy_tones_per_source_default_to_the_local_one():
    renderer, _ = make_renderer(assets={"bell": np.full(400, 0.2, dtype=np.float32)})
    local = config(courtesy_tone_style="triple")
    assert np.array_equal(renderer.render("courtesy_tone_link", local), renderer.render("courtesy_tone", local))
    assert np.array_equal(renderer.render("courtesy_tone_patch", local), renderer.render("courtesy_tone", local))

    own = config(courtesy_tone_style="triple", courtesy_tone_link_style="chirp", courtesy_tone_patch_asset_id="bell")
    assert not np.array_equal(renderer.render("courtesy_tone_link", own), renderer.render("courtesy_tone", own))
    assert np.allclose(renderer.render("courtesy_tone_patch", own), 0.2, atol=1e-3)
    assert renderer.cached_duration("courtesy_tone_patch", own) == 400 / RATE
