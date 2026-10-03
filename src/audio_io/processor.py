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

from controller.events import COSChanged, ControllerEvent, CTCSSChanged, DTMFDigit, ToneBurst
from dsp.emphasis import emphasis_kernel
from dsp.goertzel import CTCSSDetector, DTMFDetector, ToneBurstDetector
from playout.wav import lowpass_kernel

from .patch import LinkAudio, PatchAudio
from .resample import StreamFIR

CosSource = Literal["vox", "ctcss", "external"]
SILENCE_DB = -120.0
# CTCSS tones top out at 254.1 Hz; voice that matters starts around 300 Hz.
_SUBAUDIBLE_CUTOFF_HZ = 280.0
_SUBAUDIBLE_TAPS = 401
_VOX_HYSTERESIS_DB = 3.0
_CTCSS_ANALYZE_EVERY_BLOCKS = 5  # CTCSS needs a 1 s window anyway; ~100 ms decisions are plenty
# A digit is reported on its second block, and the block before those may
# hold its first few milliseconds: three blocks of delay mute it all.
_DTMF_MUTE_BLOCKS = 3
_DTMF_MUTE_HANG_BLOCKS = 2  # the tones' tail, and the gap before a next digit


@dataclass
class ProcessorSettings:
    sample_rate: int
    cos_source: CosSource = "vox"
    vox_threshold_db: float = -40.0
    vox_attack: float = 0.06  # seconds above threshold before COS opens
    vox_hold: float = 0.4  # seconds below threshold before COS closes
    tx_gain_db: float = 0.0
    tx_ctcss_hz: Optional[float] = None
    tx_ctcss_level_db: float = -20.0
    ctcss_threshold: float = 0.05
    dtmf_threshold: float = 0.05
    # False for a link radio: what it receives goes to the repeater, not back out its own transmitter.
    local_repeat: bool = True
    dtmf_mute: bool = True  # never pass DTMF tones on (transmitter, links, recordings)
    squelch_tail_ms: float = 0.0  # cut this much received audio from the end of each transmission
    tx_delay_ms: float = 0.0  # silence after keying up, before any audio
    tone_burst_ms: Optional[float] = None  # detect (and mute) 1750 Hz access bursts this long; None = off
    rx_deemphasis: bool = False
    tx_preemphasis: bool = False


class _DelayLine:
    """Received audio held back a few blocks on its way to everything
    downstream, so audio already received can still be muted: the start of
    a DTMF digit, or the squelch tail before the carrier dropped."""

    def __init__(self) -> None:
        self._blocks: "collections.deque[np.ndarray]" = collections.deque()
        self._muted: "collections.deque[bool]" = collections.deque()

    def append(self, block: np.ndarray) -> None:
        self._blocks.append(block)
        self._muted.append(False)

    def mute_last(self, count: int) -> None:
        for i in range(max(0, len(self._muted) - count), len(self._muted)):
            self._muted[i] = True

    def release(self, delay_blocks: int) -> np.ndarray:
        """The block from `delay_blocks` ago (silence if muted, or while filling up)."""
        out: Optional[np.ndarray] = None
        while len(self._blocks) > delay_blocks:
            block, muted = self._blocks.popleft(), self._muted.popleft()
            out = np.zeros_like(block) if muted else block
        if out is None:
            return np.zeros_like(self._blocks[-1])
        return out


def _highpass_kernel(cutoff_ratio: float) -> np.ndarray:
    """Spectral inversion of the low-pass: passes everything above the cutoff."""
    kernel = -lowpass_kernel(cutoff_ratio, taps=_SUBAUDIBLE_TAPS)
    kernel[_SUBAUDIBLE_TAPS // 2] += 1.0
    return kernel


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
        self._ctcss_blocks = 0
        self._dtmf = DTMFDetector(settings.sample_rate, magnitude_threshold=settings.dtmf_threshold)
        self._burst = ToneBurstDetector(settings.sample_rate)
        self._burst_hang = 0
        self._deemphasis = StreamFIR(emphasis_kernel(settings.sample_rate, pre=False))
        self._preemphasis = StreamFIR(emphasis_kernel(settings.sample_rate, pre=True))
        self._capture: Optional[list[np.ndarray]] = None
        self._capture_limit = 0
        self._subaudible_filter = StreamFIR(_highpass_kernel(_SUBAUDIBLE_CUTOFF_HZ / settings.sample_rate))
        self._ctcss_phase = 0.0
        self._patch: Optional[PatchAudio] = None
        self._link: Optional[LinkAudio] = None
        self._port: Optional[LinkAudio] = None
        self._delay = _DelayLine()
        self._dtmf_hang = 0
        self._was_keyed = False
        self._lead_blocks = 0

    @property
    def repeating_voice(self) -> bool:
        """A local user is being repeated (what the links are sent)."""
        return self.cos_open and self._repeating

    # -- controls (service thread) -----------------------------------------

    def set_ptt(self, active: bool) -> None:
        self._ptt = active

    def set_repeating(self, repeating: bool) -> None:
        """Pass receive audio through to the transmitter (controller RECEIVING)."""
        self._repeating = repeating

    def set_external_cos(self, active: bool) -> None:
        self._external_cos = active

    def set_patch(self, patch: Optional[PatchAudio]) -> None:
        """Exchange audio with an autopatch call: received audio goes to the
        phone, and phone audio is transmitted while PTT is up."""
        self._patch = patch

    def set_link(self, link: Optional[LinkAudio]) -> None:
        """Exchange audio with the AllStar node: audio being repeated goes to
        the node, and the node's audio is transmitted while PTT is up."""
        self._link = link

    def set_port(self, port: Optional[LinkAudio]) -> None:
        """The same exchange with the link radio (api.link_radio)."""
        self._port = port

    def start_capture(self, max_seconds: float) -> None:
        """Keep a copy of received audio (for recording or parrot)."""
        self._capture_limit = int(max_seconds * self.settings.sample_rate)
        self._capture = []

    def stop_capture(self) -> np.ndarray:
        captured, self._capture = self._capture, None
        if not captured:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(captured)[: self._capture_limit]

    def play(self, samples: np.ndarray) -> None:
        self._clips.append(np.asarray(samples, dtype=np.float32))

    @property
    def playing(self) -> bool:
        return self._current is not None or bool(self._clips)

    # -- audio worker thread -----------------------------------------------

    def process(self, block: np.ndarray) -> ProcessResult:
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        if self.settings.rx_deemphasis:
            block = self._deemphasis.process(block).astype(np.float32)
        events: list[ControllerEvent] = []
        self.rx_level_db = level_db(block)

        tone = self._detect_ctcss(block)
        if tone != self.ctcss_hz:
            self.ctcss_hz = tone
            events.append(CTCSSChanged(tone_hz=tone))

        self._delay.append(block)
        cos = self._carrier(block)
        if cos != self.cos_open:
            self.cos_open = cos
            events.append(COSChanged(active=cos))
            if not cos:
                self._delay.mute_last(self._tail_blocks(len(block)))

        digit = self._dtmf.process(block) if self.cos_open else None
        if digit is not None:
            events.append(DTMFDigit(digit=digit))
        self._mute_dtmf(digit)
        if self._detect_burst(block):
            events.append(ToneBurst())
        block = self._delay.release(self._delay_blocks(len(block)))
        capture = self._capture
        if capture is not None and len(capture) * len(block) < self._capture_limit:
            capture.append(block.copy())

        encode_hz = self.settings.tx_ctcss_hz
        patch = self._patch
        link = self._link
        port = self._port
        # Filtered continuously (not just while repeating) so the filter's
        # history is warm the moment repeating starts. The phone and the
        # node never get the user's CTCSS tone either.
        voice = self._subaudible_filter.process(block) if encode_hz or patch or link or port else block
        repeat_audio = voice if encode_hz else block
        out = np.zeros_like(block)
        if self._ptt and self._repeating and self.settings.local_repeat:
            out += repeat_audio * (10 ** (self.settings.tx_gain_db / 20))
        if patch is not None:
            phone = patch.exchange(voice, self.cos_open)
            if self._ptt:
                out += phone
        if link is not None:
            # Only what the controller is repeating: not a kerchunk, a
            # signal without the required CTCSS, or a parrot recording.
            node = link.exchange(voice, self.repeating_voice)
            if self._ptt:
                out += node
        if port is not None:
            far_end = port.exchange(voice, self.repeating_voice)
            if self._ptt:
                out += far_end
        # A clip keeps the transmitter keyed until it finishes, even if the
        # controller already dropped PTT (e.g. the timeout tone).
        transmitting = self._ptt or self.playing
        if transmitting and not self._was_keyed:
            self._lead_blocks = math.ceil(self.settings.tx_delay_ms / 1000 * self.settings.sample_rate / len(block) - 1e-9)
        self._was_keyed = transmitting
        if self._lead_blocks > 0:
            # Keyed, but nothing goes out yet; clips wait rather than lose their start.
            self._lead_blocks -= 1
            out.fill(0)
            return ProcessResult(out=out, transmitting=transmitting, events=events)
        clip = self._next_clip_samples(len(block))
        if clip is not None:
            out[: len(clip)] += clip
        if self.settings.tx_preemphasis:
            out = self._preemphasis.process(out).astype(np.float32)
        if not transmitting:
            out.fill(0)
        elif encode_hz:
            out += self._ctcss_tone(encode_hz, len(block))
        np.clip(out, -1.0, 1.0, out=out)
        return ProcessResult(out=out, transmitting=transmitting, events=events)

    def _tail_blocks(self, n: int) -> int:
        return math.ceil(self.settings.squelch_tail_ms / 1000 * self.settings.sample_rate / n - 1e-9)

    def _delay_blocks(self, n: int) -> int:
        mute = self.settings.dtmf_mute or self.settings.tone_burst_ms is not None
        return max(_DTMF_MUTE_BLOCKS if mute else 0, self._tail_blocks(n))

    def _detect_burst(self, block: np.ndarray) -> bool:
        """The burst opens the repeater; it isn't repeated (muted like DTMF)."""
        minimum = self.settings.tone_burst_ms
        if minimum is None:
            self._burst_hang = 0
            return False
        self._burst.min_seconds = minimum / 1000
        heard = self._burst.process(block)
        if self._burst.present:
            self._delay.mute_last(_DTMF_MUTE_BLOCKS)
            self._burst_hang = _DTMF_MUTE_HANG_BLOCKS
        elif self._burst_hang > 0:
            self._delay.mute_last(1)
            self._burst_hang -= 1
        return heard

    def _mute_dtmf(self, digit: Optional[str]) -> None:
        if not self.settings.dtmf_mute:
            self._dtmf_hang = 0
            return
        if digit is not None:
            self._delay.mute_last(_DTMF_MUTE_BLOCKS)
            self._dtmf_hang = _DTMF_MUTE_HANG_BLOCKS
        elif self.cos_open and self._dtmf.holding is not None:
            self._delay.mute_last(1)
            self._dtmf_hang = _DTMF_MUTE_HANG_BLOCKS
        elif self._dtmf_hang > 0:
            self._delay.mute_last(1)
            self._dtmf_hang -= 1

    def _ctcss_tone(self, hz: float, n: int) -> np.ndarray:
        step = 2 * math.pi * hz / self.settings.sample_rate
        phases = self._ctcss_phase + step * np.arange(n)
        self._ctcss_phase = float((phases[-1] + step) % (2 * math.pi))
        return (10 ** (self.settings.tx_ctcss_level_db / 20) * np.sin(phases)).astype(np.float32)

    def _detect_ctcss(self, block: np.ndarray) -> Optional[float]:
        self._ctcss.feed(block)
        self._ctcss_blocks += 1
        if self._ctcss_blocks < _CTCSS_ANALYZE_EVERY_BLOCKS:
            return self.ctcss_hz
        self._ctcss_blocks = 0
        return self._ctcss.decide()

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
