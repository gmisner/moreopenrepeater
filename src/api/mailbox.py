"""Voice mailbox: short messages left over the air for a station to hear later.

`*7 12 #` (codes are settings) records the next transmission into mailbox
12, instead of repeating it. `*8 12 * <PIN> #` plays the messages back, oldest
first, each introduced by when it was left; `*9 12 * <PIN> #` deletes them.
A reminder ("messages waiting for mailbox 12") goes out every so often, and
messages expire after a set number of days.

Admins set up the mailboxes (a number, whose it is, a PIN) and can listen to
and delete messages on the dashboard. Mailboxes and messages live in their own
files under `data/mailbox/`, out of the settings and backups.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from controller.events import MailboxCommand
from playout.renderer import RECORDING_PREFIX, TTS_PREFIX, ClipRenderer
from playout.wav import encode_wav, read_wav

from .persistence import StateStore
from .safe_paths import child_path
from .service import RepeaterService, spoken_time

_logger = logging.getLogger("moreopenrepeater.mailbox")

MAX_MESSAGES = 10  # per mailbox
MAX_PIN_FAILURES = 5
PIN_LOCKOUT_SECONDS = 10 * 60
PLAYBACK_KEEP_SECONDS = 10 * 60
GAP_SECONDS = 0.6
_WAV_HEADER_BYTES = 44
_MESSAGE_ID = re.compile(r"^mailbox-(\d{1,6})-(\d{13,})$")
_PLAYBACK_ID = re.compile(r"^mailbox-play-(\d{13,})$")


def spoken_box(box: str) -> str:
    """Digit by digit, the way it was keyed: "mailbox 1 2"."""
    return " ".join(box)


def is_mailbox_clip(clip_id: str) -> bool:
    return clip_id.startswith("mailbox-")


@dataclass(frozen=True)
class MessageInfo:
    id: str
    box: str
    left_at: float
    duration: float


class MailboxStore:
    """`directory=None` keeps nothing (tests that don't record)."""

    def __init__(self, directory: Optional[Path], boxes_store: Optional[StateStore], sample_rate: int = 16000) -> None:
        self.directory = directory
        self.sample_rate = sample_rate
        self._boxes_store = boxes_store
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
        self._boxes: dict[str, dict] = ((boxes_store.load() if boxes_store else None) or {}).get("boxes", {})

    # -- mailboxes ----------------------------------------------------------

    def boxes(self) -> dict[str, dict]:
        return {box: dict(info) for box, info in sorted(self._boxes.items(), key=lambda item: int(item[0]))}

    def set_box(self, box: str, name: str, pin: Optional[str]) -> None:
        """`pin=None` keeps the current one."""
        current = self._boxes.get(box, {})
        self._boxes[box] = {"name": name, "pin": pin if pin is not None else current.get("pin", "")}
        self._save_boxes()

    def delete_box(self, box: str) -> bool:
        if self._boxes.pop(box, None) is None:
            return False
        self._save_boxes()
        self.delete_messages(box)
        return True

    def _save_boxes(self) -> None:
        if self._boxes_store is not None:
            self._boxes_store.save({"boxes": self._boxes})

    # -- messages -----------------------------------------------------------

    def path_for(self, clip_id: str) -> Path:
        if self.directory is None or not (_MESSAGE_ID.match(clip_id) or _PLAYBACK_ID.match(clip_id)):
            raise KeyError(clip_id)
        return child_path(self.directory, f"{clip_id}.wav")

    def save(self, box: str, samples: np.ndarray, left_at: float) -> Optional[MessageInfo]:
        if self.directory is None:
            return None
        message_id = f"mailbox-{box}-{int(left_at * 1000)}"
        self._write(message_id, samples)
        return self._info(self.path_for(message_id))

    def save_playback(self, samples: np.ndarray, now: float) -> Optional[str]:
        if self.directory is None:
            return None
        playback_id = f"mailbox-play-{int(now * 1000)}"
        self._write(playback_id, samples)
        return playback_id

    def _write(self, clip_id: str, samples: np.ndarray) -> None:
        path = self.path_for(clip_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(encode_wav(samples, self.sample_rate))
        tmp.replace(path)

    def messages(self, box: Optional[str] = None) -> list[MessageInfo]:
        """Oldest first."""
        if self.directory is None:
            return []
        found = [self._info(p) for p in self.directory.glob("mailbox-*.wav") if _MESSAGE_ID.match(p.stem)]
        return sorted((m for m in found if box is None or m.box == box), key=lambda m: m.left_at)

    def delete_message(self, message_id: str) -> bool:
        if not _MESSAGE_ID.match(message_id):
            return False
        try:
            self.path_for(message_id).unlink()
        except (KeyError, FileNotFoundError):
            return False
        for sidecar in self.sidecars(message_id):
            sidecar.unlink(missing_ok=True)
        return True

    def delete_messages(self, box: str) -> int:
        return sum(self.delete_message(m.id) for m in self.messages(box))

    def sidecars(self, clip_id: str) -> list[Path]:
        """Files kept beside a message (its transcript)."""
        return [] if self.directory is None else [self.directory / f"{clip_id}.txt"]

    def transcript_path(self, message_id: str) -> Path:
        if not _MESSAGE_ID.match(message_id):
            raise KeyError(message_id)
        return self.path_for(message_id).with_suffix(".txt")

    def recent_ids(self, limit: int) -> list[str]:
        return [m.id for m in reversed(self.messages())][:limit]

    def prune(self, older_than: float, now: float) -> int:
        """Expires messages left before `older_than`, and playback clips that have gone out."""
        removed = sum(self.delete_message(m.id) for m in self.messages() if m.left_at < older_than)
        if self.directory is not None:
            cutoff = int((now - PLAYBACK_KEEP_SECONDS) * 1000)
            for path in self.directory.glob("mailbox-play-*.wav"):
                match = _PLAYBACK_ID.match(path.stem)
                if match and int(match.group(1)) < cutoff:
                    path.unlink(missing_ok=True)
        return removed

    def _info(self, path: Path) -> MessageInfo:
        match = _MESSAGE_ID.match(path.stem)
        assert match is not None
        return MessageInfo(
            id=path.stem,
            box=match.group(1),
            left_at=int(match.group(2)) / 1000,
            duration=max(0, path.stat().st_size - _WAV_HEADER_BYTES) / 2 / self.sample_rate,
        )


class Mailbox:
    def __init__(
        self,
        service: RepeaterService,
        store: MailboxStore,
        renderer: ClipRenderer,
        net_active: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._service = service
        self.store = store
        self._renderer = renderer
        self._net_active = net_active
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._last_reminder = clock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.audit_hook: Optional[Callable[[str, str, str], None]] = None
        service.mailbox_hook = self.handle

    def _say(self, text: str) -> None:
        self._service.speak(TTS_PREFIX + text)

    def _audit(self, action: str, box: str) -> None:
        if self.audit_hook is not None:
            self.audit_hook("DTMF", action, f"mailbox {box}")

    def _from_worker(self, fn: Callable, *args) -> None:
        """Runs `fn` on the event loop, from a worker thread (or right away without one)."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(fn, *args)
        else:
            fn(*args)

    def _in_background(self, fn: Callable, *args) -> None:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        if self._loop is not None:
            self._loop.run_in_executor(None, fn, *args)
        else:
            fn(*args)

    # -- DTMF ---------------------------------------------------------------

    def handle(self, command: MailboxCommand) -> None:
        box = command.box
        if box not in self.store.boxes():
            self._say(f"There is no mailbox {spoken_box(box)}.")
            return
        if command.action == "leave":
            self._leave(box)
            return
        if not self._pin_ok(box, command.pin):
            return
        if command.action == "play":
            self._play(box)
        elif command.action == "delete":
            count = self.store.delete_messages(box)
            self._audit("Deleted mailbox messages", box)
            self._say(f"{count} {'message' if count == 1 else 'messages'} deleted." if count else "There were no messages.")

    def _pin_ok(self, box: str, pin: str) -> bool:
        now = self._clock()
        if self._locked_until.get(box, 0) > now:
            self._say("That mailbox is locked. Try again later.")
            return False
        expected = self.store.boxes()[box].get("pin", "")
        if expected and hmac.compare_digest(expected, pin):
            self._failures.pop(box, None)
            return True
        failures = [t for t in self._failures.get(box, []) if now - t < PIN_LOCKOUT_SECONDS] + [now]
        self._failures[box] = failures
        if len(failures) >= MAX_PIN_FAILURES:
            self._locked_until[box] = now + PIN_LOCKOUT_SECONDS
            self._failures.pop(box, None)
            _logger.warning("mailbox %s locked after %d wrong PINs", box, MAX_PIN_FAILURES)
        self._audit("Wrong mailbox PIN", box)
        self._say("Wrong PIN.")
        return False

    def _leave(self, box: str) -> None:
        if len(self.store.messages(box)) >= MAX_MESSAGES:
            self._say(f"Mailbox {spoken_box(box)} is full.")
            return
        audio = self._service.audio_output
        config = self._service.config
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        if audio is None or not audio.arm_capture(config.mailbox_max_seconds, lambda samples, at: self._recorded(box, samples, at)):
            self._say("Messages need live audio, which is not running.")
            return
        self._say(f"Mailbox {spoken_box(box)}. Key up and leave your message.")

    def _recorded(self, box: str, samples: np.ndarray, left_at: float) -> None:
        """Worker thread."""
        try:
            info = self.store.save(box, samples, left_at)
        except OSError:
            _logger.exception("couldn't save a message for mailbox %s", box)
            self._from_worker(self._say, "Sorry, the message could not be saved.")
            return
        if info is not None:
            _logger.info("message left for mailbox %s (%.0f s)", box, info.duration)
            self._from_worker(self._audit, "Left a mailbox message", box)
            self._from_worker(self._say, f"Message saved for mailbox {spoken_box(box)}.")

    def _play(self, box: str) -> None:
        messages = self.store.messages(box)
        if not messages:
            self._say(f"No messages for mailbox {spoken_box(box)}.")
            return
        self._audit("Played mailbox messages", box)
        self._in_background(self._build_playback, box, messages)

    def _build_playback(self, box: str, messages: list[MessageInfo]) -> None:
        """Worker thread: one clip with every message, each introduced, so
        they go out in order in a single transmission."""
        config = self._service.config
        rate = self._renderer.sample_rate
        gap = np.zeros(int(GAP_SECONDS * rate), dtype=np.float32)
        count = len(messages)
        parts = [self._renderer.render(TTS_PREFIX + f"Mailbox {spoken_box(box)}, {count} {'message' if count == 1 else 'messages'}.", config), gap]
        try:
            for number, message in enumerate(messages, 1):
                left = datetime.fromtimestamp(message.left_at)
                parts += [self._renderer.render(TTS_PREFIX + f"Message {number}, left at {spoken_time(left)}.", config), gap]
                parts += [read_wav(self.store.path_for(message.id), rate), gap]
            parts.append(self._renderer.render(TTS_PREFIX + "End of messages.", config))
            playback_id = self.store.save_playback(np.concatenate(parts), self._clock())
        except Exception:
            _logger.exception("couldn't put together mailbox %s's messages", box)
            self._from_worker(self._say, "Sorry, the messages could not be played.")
            return
        if playback_id is not None:
            self._from_worker(self._service.speak, RECORDING_PREFIX + playback_id)

    # -- housekeeping -------------------------------------------------------

    def reminder_text(self) -> Optional[str]:
        waiting = sorted({m.box for m in self.store.messages()} & set(self.store.boxes()), key=int)
        if not waiting:
            return None
        names = [spoken_box(box) for box in waiting]
        if len(names) == 1:
            return f"Messages waiting for mailbox {names[0]}."
        return f"Messages waiting for mailboxes {', '.join(names[:-1])} and {names[-1]}."

    def prune(self) -> int:
        """Blocking file I/O: run it off the event loop."""
        now = self._clock()
        return self.store.prune(now - self._service.config.mailbox_retention_days * 86400, now)

    def remind(self) -> None:
        """Sends the reminder when it's due. On the event loop."""
        config = self._service.config
        now = self._clock()
        interval = config.mailbox_reminder_minutes * 60
        if not config.mailbox_enabled or interval <= 0 or now - self._last_reminder < interval:
            return
        self._last_reminder = now
        text = self.reminder_text()
        if text and not self._net_active():
            self._say(text)
