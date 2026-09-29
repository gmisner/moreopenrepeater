"""Audio crossing between the radio (audio worker thread) and a phone call
(event loop), both at the processing sample rate.

Each 20 ms radio block goes to the phone side, and the same amount of phone
audio comes back to be transmitted. The two sides run on different clocks
(the sound card's and the network's), so each direction is a small FIFO:

  - radio -> phone keeps only the newest few blocks, so a sender that fell
    behind catches up instead of building delay.
  - phone -> radio waits for a little audio to build up before playing
    (and again after running dry), which absorbs network jitter, and drops
    the oldest audio past a limit.

deque append/popleft are atomic in CPython; `_pending` and `_primed` are
only touched from the audio thread.
"""
from __future__ import annotations

import collections
from typing import Optional

import numpy as np

MAX_RADIO_BLOCKS = 3
PRIME_SECONDS = 0.06
MAX_PHONE_SECONDS = 0.5


class PatchAudio:
    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        self._to_phone: "collections.deque[np.ndarray]" = collections.deque(maxlen=MAX_RADIO_BLOCKS)
        self._from_phone: "collections.deque[np.ndarray]" = collections.deque()
        # Samples in _from_phone = added - dropped - consumed; each counter
        # has a single writer thread, since `+=` isn't atomic.
        self._added = 0
        self._dropped = 0
        self._consumed = 0
        self._pending = np.zeros(0, dtype=np.float32)
        self._primed = False

    @property
    def _queued(self) -> int:
        return self._added - self._dropped - self._consumed

    # -- event loop side ----------------------------------------------------

    def take_radio(self) -> Optional[np.ndarray]:
        """The oldest radio block not yet sent to the phone, or None."""
        try:
            return self._to_phone.popleft()
        except IndexError:
            return None

    def add_phone(self, samples: np.ndarray) -> None:
        samples = np.asarray(samples, dtype=np.float32)
        self._from_phone.append(samples)
        self._added += len(samples)
        while self._queued > MAX_PHONE_SECONDS * self.sample_rate and len(self._from_phone) > 1:
            try:
                self._dropped += len(self._from_phone.popleft())
            except IndexError:  # the audio thread took it first
                break

    # -- audio thread side --------------------------------------------------

    def exchange(self, radio: np.ndarray, carrier: bool) -> np.ndarray:
        """Hand over one radio block (silence unless a user is transmitting)
        and get the same number of phone samples back."""
        self._offer(radio, carrier)
        n = len(radio)
        if not self._primed:
            if self._queued + len(self._pending) < PRIME_SECONDS * self.sample_rate:
                return np.zeros(n, dtype=np.float32)
            self._primed = True
        while len(self._pending) < n:
            try:
                chunk = self._from_phone.popleft()
            except IndexError:
                break
            self._consumed += len(chunk)
            self._pending = np.concatenate([self._pending, chunk])
        out = np.zeros(n, dtype=np.float32)
        take = min(n, len(self._pending))
        out[:take] = self._pending[:take]
        self._pending = self._pending[take:]
        if take < n:
            self._primed = False
        return out

    def _offer(self, radio: np.ndarray, carrier: bool) -> None:
        self._to_phone.append(radio.copy() if carrier else np.zeros_like(radio))


class LinkAudio(PatchAudio):
    """The same crossing for an AllStar node's audio (the "phone" side is
    the node). Radio blocks go over only while there's a signal, since the
    node treats audio arriving as its receiver being keyed."""

    def _offer(self, radio: np.ndarray, carrier: bool) -> None:
        if carrier:
            self._to_phone.append(radio.copy())
