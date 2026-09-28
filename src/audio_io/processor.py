"""Per-block audio logic for the live engine, with no threads or devices.

Each 20 ms receive block goes in; out come the transmit block and any
controller events (carrier up/down, CTCSS tone changes, DTMF digits).
Keeping this free of PortAudio and threading means it's tested with plain
synthetic numpy signals.

Software COS ("carrier operated squelch") options, for radios/interfaces
without a COS wire:
  - "vox": the receiver's squelched audio is silent until someone keys up,
    so audio level above a threshold (with attack/hold timing) = carrier.
  - "ctcss": carrier = a CTCSS tone is being received.
  - "external": something else (a CM108 GPIO pin) calls `set_external_cos`.

The controller decides *what* to transmit; this only carries it out:
`set_ptt` / `set_repeating` / `play` are called from the service thread,
`process` from the audio worker thread. Attribute writes and deque
append/popleft are atomic in CPython, so no locks are needed.
"""
from __future__ import annotations

import collections
import math
from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np

from controller.events import COSChanged, ControllerEvent, CTCSSChanged, DTMFDigit
from dsp.goertzel import CTCSSDetector, DTMFDetector

CosSource = Literal["vox", "ctcss", "external"]
SILENCE_DB = -120.0
_VOX_HYSTERESIS_DB = 3.0
_CTCSS_ANALYZE_EVERY_BLOCKS = 5  # CTCSS needs a 1 s window anyway; ~100 ms steps are plenty


@dataclass
class ProcessorSettings:
    sample_rate: int
    cos_source: CosSource = "vox"
    vox_threshold_db: float = -40.0
    vox_attack: float = 0.06  # seconds above threshold before COS opens
    vox_hold: float = 0.4  # seconds below threshold before COS closes
    tx_gain_db: float = 0.0
    ctcss_threshold: float = 0.05
    dtmf_threshold: float = 0.05


def level_db(block: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64)))) if len(block) else 0.0
    return 20 * math.log10(rms) if rms > 1e-6 else SILENCE_DB


@dataclass
class ProcessResult:
    out: np.ndarray
    transmitting: bool
    events: list[ControllerEvent] = field(default_factory=list)


class AudioProcessor:
    def __init__(self, settings: ProcessorSettings) -> None:
        self.settings = settings
        self.rx_level_db = SILENCE_DB
        self.cos_open = False
        self.ctcss_hz: Optional[float] = None
        self._ptt = False
        self._repeating = False
        self._external_cos = False
        self._above = 0.0
        self._below = 0.0
        self._clips: "collections.deque[np.ndarray]" = collections.deque()
        self._current: Optional[np.ndarray] = None
        self._position = 0
        self._ctcss = CTCSSDetector(settings.sample_rate, magnitude_threshold=settings.ctcss_threshold)
        self._ctcss_pending: list[np.ndarray] = []
        self._dtmf = DTMFDetector(settings.sample_rate, magnitude_threshold=settings.dtmf_threshold)

    # -- controls (service thread) -----------------------------------------

    def set_ptt(self, active: bool) -> None:
        self._ptt = active

    def set_repeating(self, repeating: bool) -> None:
        """Pass receive audio through to the transmitter (controller RECEIVING)."""
        self._repeating = repeating

    def set_external_cos(self, active: bool) -> None:
        self._external_cos = active

    def play(self, samples: np.ndarray) -> None:
        self._clips.append(np.asarray(samples, dtype=np.float32))

    @property
    def playing(self) -> bool:
        return self._current is not None or bool(self._clips)

    # -- audio worker thread -----------------------------------------------

    def process(self, block: np.ndarray) -> ProcessResult:
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        events: list[ControllerEvent] = []
        self.rx_level_db = level_db(block)

        tone = self._detect_ctcss(block)
        if tone != self.ctcss_hz:
            self.ctcss_hz = tone
            events.append(CTCSSChanged(tone_hz=tone))

        cos = self._carrier(block)
        if cos != self.cos_open:
            self.cos_open = cos
            events.append(COSChanged(active=cos))

        if self.cos_open:
            digit = self._dtmf.process(block)
            if digit is not None:
                events.append(DTMFDigit(digit=digit))

        out = np.zeros_like(block)
        if self._ptt and self._repeating:
            out += block * (10 ** (self.settings.tx_gain_db / 20))
        clip = self._next_clip_samples(len(block))
        if clip is not None:
            out[: len(clip)] += clip
        # A clip keeps the transmitter keyed until it finishes, even if the
        # controller already dropped PTT (e.g. the timeout tone).
        transmitting = self._ptt or clip is not None
        if not transmitting:
            out.fill(0)
        np.clip(out, -1.0, 1.0, out=out)
        return ProcessResult(out=out, transmitting=transmitting, events=events)

    def _detect_ctcss(self, block: np.ndarray) -> Optional[float]:
        self._ctcss_pending.append(block)
        if len(self._ctcss_pending) < _CTCSS_ANALYZE_EVERY_BLOCKS:
            return self.ctcss_hz
        chunk = np.concatenate(self._ctcss_pending)
        self._ctcss_pending = []
        return self._ctcss.process(chunk)

    def _carrier(self, block: np.ndarray) -> bool:
        source = self.settings.cos_source
        if source == "external":
            return self._external_cos
        if source == "ctcss":
            return self.ctcss_hz is not None
        block_seconds = len(block) / self.settings.sample_rate
        threshold = self.settings.vox_threshold_db - (_VOX_HYSTERESIS_DB if self.cos_open else 0.0)
        if self.rx_level_db >= threshold:
            self._above += block_seconds
            self._below = 0.0
        else:
            self._below += block_seconds
            self._above = 0.0
        if not self.cos_open:
            return self._above >= self.settings.vox_attack - 1e-9
        return self._below < self.settings.vox_hold - 1e-9

    def _next_clip_samples(self, n: int) -> Optional[np.ndarray]:
        if self._current is None:
            if not self._clips:
                return None
            self._current = self._clips.popleft()
            self._position = 0
        chunk = self._current[self._position : self._position + n]
        self._position += n
        if self._position >= len(self._current):
            self._current = None
        return chunk
