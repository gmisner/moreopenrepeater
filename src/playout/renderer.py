"""Renders a controller clip name into samples, given the current config.

Clip names are the strings carried by `PlayAudio(clip=...)`:
  - "courtesy_tone", "timeout_tone", "id" -- the controller's built-ins,
    using an assigned uploaded asset when there is one;
  - "id_long" -- the long ID, which can say the time (so it's cached per
    minute, and re-warmed by the app while it's turned on);
  - "courtesy_tone_link", "courtesy_tone_patch" -- the courtesy tone after
    a linked station or a phone call, if set differently;
  - "asset:<id>" -- an uploaded clip, played as-is;
  - "tts:<text>" -- arbitrary text through the local TTS engine, with
    "{callsign}" spelled out (announcements and weather alerts use this);
  - "recording:<id>" -- a saved transmission (parrot plays these back).

Rendering can be slow (TTS spawns a subprocess), so results are cached and
`cached_duration` only ever answers from the cache -- it's what the
controller calls from inside the event loop to size the ID state, and it
must never block. `warm()` pre-renders the built-ins off the event loop.
"""
from __future__ import annotations

import collections
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Hashable, Optional

import numpy as np

from controller.state_machine import RepeaterConfig
from dsp.morse import morse_tone

from .tones import courtesy_tone, timeout_tone
from .tts import TTSEngine, TTSError, format_voice_id, spoken_time
from .wav import WavError, read_wav, resample

ASSET_PREFIX = "asset:"
TTS_PREFIX = "tts:"
RECORDING_PREFIX = "recording:"
COURTESY_CLIPS = {"courtesy_tone": "", "courtesy_tone_link": "link", "courtesy_tone_patch": "patch"}
BUILTIN_CLIPS = (*COURTESY_CLIPS, "id", "id_long", "timeout_tone")
SPEECH_PEAK = 0.5
_GAP_SECONDS = 0.4

_logger = logging.getLogger("moreopenrepeater.playout")


class UnknownClipError(KeyError):
    pass


def courtesy_sound(clip: str, config: RepeaterConfig) -> tuple[str, Optional[str]]:
    """(built-in style, uploaded clip id or None) for one of COURTESY_CLIPS."""
    source = COURTESY_CLIPS[clip]
    if source:
        style = getattr(config, f"courtesy_tone_{source}_style")
        asset = getattr(config, f"courtesy_tone_{source}_asset_id")
        if asset or style != "same":
            return (config.courtesy_tone_style if style == "same" else style), asset
    return config.courtesy_tone_style, config.courtesy_tone_asset_id


class ClipRenderer:
    def __init__(
        self,
        asset_path: Callable[[str], Path],
        tts: Optional[TTSEngine],
        sample_rate: int = 16000,
        cache_size: int = 32,
        recording_path: Optional[Callable[[str], Path]] = None,
        wall_clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._asset_path = asset_path
        self._wall_clock = wall_clock
        self._recording_path = recording_path
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
        self._store(key, samples)
        return samples

    def _store(self, key: tuple, samples: np.ndarray) -> None:
        with self._lock:
            self._cache[key] = samples
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

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
            if clip == "id_long":
                continue
            try:
                self.render(clip, config)
            except Exception:
                _logger.exception("pre-rendering %s failed", clip)
        self.warm_long_id(config)

    def warm_long_id(self, config: RepeaterConfig) -> None:
        """Have the long ID ready for this minute and the next, since it can
        say the time; call it at least every minute while the long ID is on.
        Older minutes are dropped so they don't push other clips out."""
        if config.long_id_mode == "off":
            return
        now = self._wall_clock()
        wanted: set[tuple] = set()
        for at in (now, now + timedelta(minutes=1)):
            parts = self._id_parts("id_long", config, at)
            key = self._id_key("id_long", parts, config)
            wanted.add(key)
            with self._lock:
                if key in self._cache:
                    continue
            try:
                self._store(key, self._render_id(*parts, config))
            except Exception:
                _logger.exception("pre-rendering id_long failed")
        with self._lock:
            for key in [k for k in self._cache if k[0] == "id_long" and k not in wanted]:
                del self._cache[key]

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
        if clip in COURTESY_CLIPS:
            style, asset = courtesy_sound(clip, config)
            return ("courtesy_tone", style, config.courtesy_tone_duration, asset, self._asset_version(asset))
        if clip == "timeout_tone":
            asset = config.timeout_tone_asset_id
            return (clip, asset, self._asset_version(asset))
        if clip in ("id", "id_long"):
            return self._id_key(clip, self._id_parts(clip, config), config)
        if clip.startswith(ASSET_PREFIX):
            asset = clip.removeprefix(ASSET_PREFIX)
            return (ASSET_PREFIX, asset, self._asset_version(asset))
        if clip.startswith(TTS_PREFIX):
            return (TTS_PREFIX, clip.removeprefix(TTS_PREFIX), config.tts_voice, config.callsign, config.id_phonetic)
        if clip.startswith(RECORDING_PREFIX) and self._recording_path is not None:
            return (RECORDING_PREFIX, clip.removeprefix(RECORDING_PREFIX))
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
        if clip in COURTESY_CLIPS:
            style, asset_id = courtesy_sound(clip, config)
            asset = self._load_asset(asset_id)
            if asset is not None:
                return asset
            return courtesy_tone(style, config.courtesy_tone_duration, self.sample_rate)
        if clip == "timeout_tone":
            asset = self._load_asset(config.timeout_tone_asset_id)
            return asset if asset is not None else timeout_tone(self.sample_rate)
        if clip in ("id", "id_long"):
            return self._render_id(*self._id_parts(clip, config), config)
        if clip.startswith(ASSET_PREFIX):
            asset = self._load_asset(clip.removeprefix(ASSET_PREFIX))
            if asset is None:
                raise UnknownClipError(clip)
            return asset
        if clip.startswith(TTS_PREFIX):
            text = format_voice_id(clip.removeprefix(TTS_PREFIX), config.callsign, config.id_phonetic)
            return self.speak(text, config.tts_voice)
        if clip.startswith(RECORDING_PREFIX) and self._recording_path is not None:
            try:
                return read_wav(self._recording_path(clip.removeprefix(RECORDING_PREFIX)), self.sample_rate)
            except (KeyError, OSError, WavError):
                raise UnknownClipError(clip) from None
        raise UnknownClipError(clip)

    def _id_parts(
        self, clip: str, config: RepeaterConfig, at: Optional[datetime] = None
    ) -> tuple[str, Optional[str], str]:
        """(mode, uploaded clip, text with the time filled in) for "id" or "id_long"."""
        if clip == "id":
            return config.id_mode, config.id_asset_id, config.voice_id_text
        text = config.long_id_text
        if "{time}" in text:
            text = text.replace("{time}", spoken_time(at or self._wall_clock()))
        return ("voice" if config.long_id_mode == "off" else config.long_id_mode), config.long_id_asset_id, text

    def _id_key(self, clip: str, parts: tuple[str, Optional[str], str], config: RepeaterConfig) -> tuple:
        mode, asset, text = parts
        return (
            clip, config.callsign, config.cw_id_suffix, mode, config.cw_wpm, config.cw_tone_hz, asset,
            self._asset_version(asset), text, config.id_phonetic, config.tts_voice, self.tts is not None,
        )

    def _voice_id(self, asset_id: Optional[str], template: str, config: RepeaterConfig) -> Optional[np.ndarray]:
        asset = self._load_asset(asset_id)
        if asset is not None:
            return asset
        text = format_voice_id(template, config.callsign, config.id_phonetic)
        if not text or self.tts is None:
            return None
        try:
            return self.speak(text, config.tts_voice)
        except TTSError:
            _logger.exception("voice ID text-to-speech failed")
            return None

    def _render_id(self, mode: str, asset_id: Optional[str], template: str, config: RepeaterConfig) -> np.ndarray:
        parts: list[np.ndarray] = []
        want_cw = mode in ("cw", "both")
        if mode in ("voice", "both"):
            voice = self._voice_id(asset_id, template, config)
            if voice is not None:
                parts.append(voice)
            elif mode == "voice":
                _logger.warning("no voice ID clip or text-to-speech available; sending the ID in CW instead")
                want_cw = True
        if want_cw and config.callsign:
            cw = morse_tone(config.callsign + config.cw_id_suffix, config.cw_wpm, config.cw_tone_hz, self.sample_rate)
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
