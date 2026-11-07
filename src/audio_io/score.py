"""Score a fixed RX clip for commissioning (Graywolf-style record→decode).

Pure numpy / DSP — no devices. Feed a recorded WAV (or a live capture) back
through the same level and CTCSS helpers the live engine uses so A/B gain
changes are comparable without waiting for new on-air traffic.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from audio_io.openings import OpeningTracker
from audio_io.processor import SILENCE_DB, level_db
from dsp.goertzel import CTCSSDetector

DEFAULT_BLOCK_MS = 20
CLIPPING_PEAK = 0.98  # absolute sample peak; above this is treated as clipping


def score_clip(
    samples: np.ndarray,
    sample_rate: int,
    *,
    block_ms: int = DEFAULT_BLOCK_MS,
) -> dict:
    """Return peak/RMS dBFS, CTCSS, and voice-band tone stats for `samples`."""
    # float64 for CTCSS / spectrum accuracy; float32 clips can miss soft tones.
    samples = np.asarray(samples, dtype=np.float64).reshape(-1)
    if sample_rate < 1000:
        raise ValueError("sample_rate must be at least 1000 Hz")
    duration = len(samples) / sample_rate if sample_rate else 0.0
    notes: list[str] = []
    if len(samples) == 0:
        return {
            "duration_seconds": 0.0,
            "sample_rate": sample_rate,
            "peak_dbfs": SILENCE_DB,
            "rms_dbfs": SILENCE_DB,
            "median_block_dbfs": SILENCE_DB,
            "clipping": False,
            "ctcss_hz": None,
            "strongest_hz": None,
            "tone_share": 0.0,
            "notes": ["clip is empty"],
        }

    peak_abs = float(np.max(np.abs(samples)))
    clipping = peak_abs >= CLIPPING_PEAK
    peak_dbfs = 20 * math.log10(peak_abs) if peak_abs > 1e-6 else SILENCE_DB
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    rms_dbfs = 20 * math.log10(rms) if rms > 1e-6 else SILENCE_DB

    block_size = max(1, int(sample_rate * block_ms / 1000))
    opening = OpeningTracker(sample_rate)
    ctcss = CTCSSDetector(sample_rate=sample_rate)
    block_dbs: list[float] = []
    last_ctcss: Optional[float] = None

    first = samples[:block_size]
    opening.start(level_db(first) if len(first) else SILENCE_DB, None)

    for start in range(0, len(samples) - block_size + 1, block_size):
        block = samples[start : start + block_size]
        db = level_db(block)
        block_dbs.append(db)
        tone = ctcss.process(block)
        if tone is not None:
            last_ctcss = tone
        opening.feed(block, db, tone, None)

    finished = opening.finish()
    median_block = float(np.median(block_dbs)) if block_dbs else SILENCE_DB

    if clipping:
        notes.append("sample peak near full scale — lower Mic / RX gain")
    if rms_dbfs <= -50:
        notes.append("very quiet — raise Mic gain or radio volume")
    elif rms_dbfs >= -6:
        notes.append("hot overall level — watch for clipping on peaks")
    if last_ctcss is None and duration >= 1.0:
        notes.append("no CTCSS locked — needs flat/discriminator audio if you rely on tone access")
    if finished.tone_share >= 0.7 and finished.strongest_hz is not None:
        notes.append(
            f"steady tone near {finished.strongest_hz:.0f} Hz "
            f"({finished.tone_share * 100:.0f}% of voice-band power)"
        )

    return {
        "duration_seconds": round(duration, 3),
        "sample_rate": sample_rate,
        "peak_dbfs": round(peak_dbfs, 2),
        "rms_dbfs": round(rms_dbfs, 2),
        "median_block_dbfs": round(median_block, 2),
        "clipping": clipping,
        "ctcss_hz": last_ctcss,
        "strongest_hz": None if finished.strongest_hz is None else round(float(finished.strongest_hz), 1),
        "tone_share": round(float(finished.tone_share), 3),
        "notes": notes,
    }
