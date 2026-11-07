import math

import numpy as np

from audio_io.score import score_clip
from dsp.goertzel import ctcss_tone


def test_score_clip_reports_silence_on_empty():
    result = score_clip(np.zeros(0, dtype=np.float32), 8000)
    assert result["duration_seconds"] == 0.0
    assert result["clipping"] is False
    assert "empty" in result["notes"][0]


def test_score_clip_rms_and_peak_for_tone():
    rate = 8000
    tone = 0.25 * np.sin(2 * np.pi * 1000 * np.arange(rate) / rate).astype(np.float32)

    result = score_clip(tone, rate)

    assert result["duration_seconds"] == 1.0
    assert result["clipping"] is False
    # 0.25 peak ≈ -12 dBFS; RMS of sine is peak/√2 ≈ -15 dBFS
    assert -13.5 < result["peak_dbfs"] < -11.5
    assert -16.5 < result["rms_dbfs"] < -14.0
    assert result["strongest_hz"] is not None
    assert abs(result["strongest_hz"] - 1000) < 30


def test_score_clip_detects_clipping():
    samples = np.ones(8000, dtype=np.float32)
    result = score_clip(samples, 8000)
    assert result["clipping"] is True
    assert any("full scale" in note for note in result["notes"])


def test_score_clip_detects_ctcss():
    rate = 8000
    # Two seconds so CTCSSDetector can fill its 1 s window and lock.
    samples = ctcss_tone(100.0, rate, rate * 2, amplitude=0.3)

    result = score_clip(samples, rate)

    assert result["ctcss_hz"] == 100.0
    assert result["duration_seconds"] == 2.0
    assert math.isfinite(result["median_block_dbfs"])
