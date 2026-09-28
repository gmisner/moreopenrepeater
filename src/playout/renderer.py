"""Renders a controller clip name into samples, given the current config.

Clip names are the strings carried by `PlayAudio(clip=...)`:
  - "courtesy_tone", "timeout_tone", "id" -- the controller's built-ins,
    using an assigned uploaded asset when there is one;
  - "asset:<id>" -- an uploaded clip, played as-is;
  - "tts:<text>" -- arbitrary text through the local TTS engine, with
    "{callsign}" spelled out (announcements and weather alerts use this).

Rendering can be slow (TTS spawns a subprocess), so results are cached and
`cached_duration` only ever answers from the cache -- it's what the
controller calls from inside the event loop to size the ID state, and it
must never block. `warm()` pre-renders the built-ins off the event loop.
"""
from __future__ import annotations

import collections
import logging
import threading
from pathlib import Path
from typing import Callable, Hashable, Optional

import numpy as np

from controller.state_machine import RepeaterConfig
from dsp.morse import morse_tone

from .tones import courtesy_tone, timeout_tone
from .tts import TTSEngine, TTSError, format_voice_id
from .wav import WavError, read_wav, resample

ASSET_PREFIX = "asset:"
TTS_PREFIX = "tts:"
BUILTIN_CLIPS = ("courtesy_tone", "id", "timeout_tone")
SPEECH_PEAK = 0.5
_GAP_SECONDS = 0.4

_logger = logging.getLogger("moreopenrepeater.playout")


class UnknownClipError(KeyError):
    pass


class ClipRenderer:
    def __init__(
        self,
        asset_path: Callable[[str], Path],
        tts: Optional[TTSEngine],
        sample_rate: int = 16000,
        cache_size: int = 32,
    ) -> None:
        self._asset_path = asset_path
        self.tts = tts
        self.sample_rate = sample_rate
        self._cache_size = cache_size
        self._cache: "collections.OrderedDict[Hashable, np.ndarray]" = collections.OrderedDict()
        self._lock = threading.Lock()

    # -- public API -------------------------------------------------------

    def render(self, clip: str, config: RepeaterConfig) -> np.ndarray:
        key = self._cache_key(clip, config)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        samples = self._render_uncached(clip, config)
        with self._lock:
            self._cache[key] = samples
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return samples

    def cached_samples(self, clip: str, config: RepeaterConfig) -> Optional[np.ndarray]:
        """The rendered clip if it's already cached, else None -- never blocks."""
        try:
            key = self._cache_key(clip, config)
        except UnknownClipError:
            return None
        with self._lock:
            return self._cache.get(key)

    def cached_duration(self, clip: str, config: RepeaterConfig) -> Optional[float]:
        samples = self.cached_samples(clip, config)
        return None if samples is None else len(samples) / self.sample_rate

    def warm(self, config: RepeaterConfig) -> None:
        for clip in BUILTIN_CLIPS:
            try:
                self.render(clip, config)
            except Exception:
                _logger.exception("pre-rendering %s failed", clip)

    def speak(self, text: str, voice: str = "") -> np.ndarray:
        if self.tts is None:
            raise TTSError("no text-to-speech engine is installed")
        samples, rate = self.tts.synthesize(text, voice)
        samples = resample(samples, rate, self.sample_rate)
        peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
        return (samples * (SPEECH_PEAK / peak)).astype(np.float32) if peak > 0 else samples

    # -- internals --------------------------------------------------------

    def _asset_version(self, asset_id: Optional[str]) -> Optional[float]:
        if not asset_id:
            return None
        path = self._asset_path(asset_id)
        return path.stat().st_mtime if path.exists() else None

    def _cache_key(self, clip: str, config: RepeaterConfig) -> Hashable:
        if clip == "courtesy_tone":
            asset = config.courtesy_tone_asset_id
            return (clip, config.courtesy_tone_style, config.courtesy_tone_duration, asset, self._asset_version(asset))
        if clip == "timeout_tone":
            asset = config.timeout_tone_asset_id
            return (clip, asset, self._asset_version(asset))
        if clip == "id":
            asset = config.id_asset_id
            return (
                clip, config.callsign, config.id_mode, config.cw_wpm, config.cw_tone_hz, asset,
                self._asset_version(asset), config.voice_id_text, config.id_phonetic, config.tts_voice,
                self.tts is not None,
            )
        if clip.startswith(ASSET_PREFIX):
            asset = clip.removeprefix(ASSET_PREFIX)
            return (ASSET_PREFIX, asset, self._asset_version(asset))
        if clip.startswith(TTS_PREFIX):
            return (TTS_PREFIX, clip.removeprefix(TTS_PREFIX), config.tts_voice, config.callsign, config.id_phonetic)
        raise UnknownClipError(clip)

    def _load_asset(self, asset_id: Optional[str]) -> Optional[np.ndarray]:
        if not asset_id:
            return None
        path = self._asset_path(asset_id)
        try:
            return read_wav(path, self.sample_rate)
        except (OSError, WavError):
            _logger.warning("couldn't load audio asset %s (%s); using the built-in sound", asset_id, path, exc_info=True)
            return None

    def _render_uncached(self, clip: str, config: RepeaterConfig) -> np.ndarray:
        if clip == "courtesy_tone":
            asset = self._load_asset(config.courtesy_tone_asset_id)
            if asset is not None:
                return asset
            return courtesy_tone(config.courtesy_tone_style, config.courtesy_tone_duration, self.sample_rate)
        if clip == "timeout_tone":
            asset = self._load_asset(config.timeout_tone_asset_id)
            return asset if asset is not None else timeout_tone(self.sample_rate)
        if clip == "id":
            return self._render_id(config)
        if clip.startswith(ASSET_PREFIX):
            asset = self._load_asset(clip.removeprefix(ASSET_PREFIX))
            if asset is None:
                raise UnknownClipError(clip)
            return asset
        if clip.startswith(TTS_PREFIX):
            text = format_voice_id(clip.removeprefix(TTS_PREFIX), config.callsign, config.id_phonetic)
            return self.speak(text, config.tts_voice)
        raise UnknownClipError(clip)

    def _voice_id(self, config: RepeaterConfig) -> Optional[np.ndarray]:
        asset = self._load_asset(config.id_asset_id)
        if asset is not None:
            return asset
        text = format_voice_id(config.voice_id_text, config.callsign, config.id_phonetic)
        if not text or self.tts is None:
            return None
        try:
            return self.speak(text, config.tts_voice)
        except TTSError:
            _logger.exception("voice ID text-to-speech failed")
            return None

    def _render_id(self, config: RepeaterConfig) -> np.ndarray:
        parts: list[np.ndarray] = []
        want_cw = config.id_mode in ("cw", "both")
        if config.id_mode in ("voice", "both"):
            voice = self._voice_id(config)
            if voice is not None:
                parts.append(voice)
            elif config.id_mode == "voice":
                _logger.warning("no voice ID clip or text-to-speech available; sending the ID in CW instead")
                want_cw = True
        if want_cw and config.callsign:
            cw = morse_tone(config.callsign, config.cw_wpm, config.cw_tone_hz, self.sample_rate)
            parts.append(cw.astype(np.float32))
        if not parts:
            _logger.warning("station ID is empty -- set a callsign")
            return np.zeros(0, dtype=np.float32)
        gap = np.zeros(int(_GAP_SECONDS * self.sample_rate), dtype=np.float32)
        joined: list[np.ndarray] = []
        for part in parts:
            if joined:
                joined.append(gap)
            joined.append(part)
        return np.concatenate(joined)
