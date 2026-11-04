"""Macros, announcements, speak/render queueing, and DTMF action dispatch."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional

from controller.announcements import Announcement, AnnouncementScheduler
from controller.events import CodedCommand, MailboxCommand, RunAction, SendLinkCommand
from controller.macros import Macro
from controller.remote_base import summary as remote_base_summary
from controller.spoken_help import help_text
from playout.renderer import ASSET_PREFIX, TTS_PREFIX

from .snapshot import MAX_HELD_ANNOUNCEMENTS, talking_clock_text

if TYPE_CHECKING:
    from .facade import RepeaterService

_logger = logging.getLogger("moreopenrepeater.service")


class Programming:
    """Macros, scheduled announcements, speak queueing, and RunAction dispatch.

    Feature hooks that macros invoke live here rather than on the facade.
    """

    def __init__(self, host: "RepeaterService", scheduler: AnnouncementScheduler) -> None:
        self._host = host
        self.scheduler = scheduler
        self._held_announcements: list[str] = []
        self._action_source = "DTMF"
        self.audit_hook: Optional[Callable[[str, str, str], None]] = None  # (actor, action, detail)
        self.aprs_summary: Optional[Callable[[], str]] = None
        self.gpio_command: Optional[Callable[[str], str]] = None  # runs a gpio macro, returns what to say
        self.net_hook: Optional[Callable[[str, str], None]] = None  # ("net_start" | "net_end", who asked)
        self.code_checker: Optional[Callable[[str], Optional[str]]] = None  # one-time code -> whose it is
        self.codes_locked: Callable[[], bool] = lambda: False
        self.homeassistant_hook: Optional[Callable[[str, str, str], None]] = None  # (target, source, pattern)
        self.metar_hook: Optional[Callable[[str], None]] = None  # airport code, or "" for all
        self.remote_base_hook: Optional[Callable[[str, str], None]] = None  # (remote_* action, keyed argument)
        self.mailbox_hook: Optional[Callable[[MailboxCommand], None]] = None

    def run_macro(self, pattern: str, source: str) -> bool:
        """Run a saved macro as if its code had been dialed; `source` names what ran it."""
        macro = next((m for m in self.list_macros() if m.pattern == pattern), None)
        if macro is None:
            _logger.warning("%s: there's no macro %r", source, pattern)
            return False
        self._action_source = source
        try:
            self._host._apply_commands([macro.build_command()])
        finally:
            self._action_source = "DTMF"
        return True

    def run_coded(self, coded: CodedCommand) -> None:
        host = self._host
        user = self.code_checker(coded.code) if self.code_checker is not None else None
        if user is None:
            locked = self.codes_locked()
            _logger.warning("macro %s: one-time code rejected%s", coded.pattern, " (locked out)" if locked else "")
            if self.audit_hook is not None:
                self.audit_hook("DTMF", "DTMF code rejected", coded.pattern)
            host.speak(TTS_PREFIX + ("Codes are locked out. Try again later." if locked else "Code rejected."))
            return
        source = f"DTMF ({user})"
        if isinstance(coded.command, SendLinkCommand) and self.audit_hook is not None:
            self.audit_hook(source, f"{source} link", coded.command.command)
        self._action_source = source
        try:
            host._apply_commands([coded.command])
        finally:
            self._action_source = "DTMF"

    def run_mailbox(self, action: MailboxCommand) -> None:
        if self.mailbox_hook is None:
            self._host.speak(TTS_PREFIX + "The mailbox is not available.")
        else:
            self.mailbox_hook(action)

    def run_action(self, action: RunAction) -> None:
        host = self._host
        source = self._action_source
        _logger.info("%s action %s(%r)", source, action.action, action.argument)
        if self.audit_hook is not None:
            self.audit_hook(source, f"{source} {action.action}", action.argument)
        if action.action == "tx_disable":
            host.update_config(transmitter_enabled=False)
        elif action.action == "tx_enable":
            host.update_config(transmitter_enabled=True)
            host.speak(TTS_PREFIX + "Transmitter enabled")
        elif action.action in ("link_radio_on", "link_radio_off"):
            on = action.action == "link_radio_on"
            host.update_config(link_radio_enabled=on)
            if host.config.link_radio_mode == "remote_base":
                host.speak(
                    TTS_PREFIX
                    + (
                        f"Remote base on, {remote_base_summary(host.config.remote_base_mhz, host.config.remote_base_shift, host.config.remote_base_tone_hz)}."
                        if on
                        else "Remote base off."
                    )
                )
            else:
                host.speak(TTS_PREFIX + ("Link radio on" if on else "Link radio off"))
        elif action.action.startswith("remote_"):
            if self.remote_base_hook is None:
                host.speak(TTS_PREFIX + "The remote base is not set up.")
            else:
                self.remote_base_hook(action.action, action.argument)
        elif action.action == "time":
            host.speak(TTS_PREFIX + talking_clock_text(host.wall_now()))
        elif action.action == "weather":
            host.speak(TTS_PREFIX + host.weather_summary_text())
        elif action.action == "id":
            host.speak("id")
        elif action.action == "say" and action.argument:
            host.speak(TTS_PREFIX + action.argument)
        elif action.action == "announcement":
            announcement = next((a for a in self.scheduler.list() if a.id == action.argument), None)
            if announcement is None:
                _logger.warning("DTMF macro refers to unknown announcement %r", action.argument)
            else:
                host.speak(self.announcement_clip(announcement))
        elif action.action == "aprs":
            text = self.aprs_summary() if self.aprs_summary else "The A P R S map is not set up."
            host.speak(TTS_PREFIX + text)
        elif action.action == "gpio":
            text = self.gpio_command(action.argument) if self.gpio_command else "That output is not set up."
            host.speak(TTS_PREFIX + text)
        elif action.action == "lockout_clear":
            if host.controller.locked_out:
                host.clear_lockout()
                host.speak(TTS_PREFIX + "Lockout cleared")
        elif action.action in ("net_start", "net_end"):
            if self.net_hook is None:
                _logger.warning("net mode isn't available")
            else:
                self.net_hook(action.action, source)
        elif action.action == "homeassistant":
            if self.homeassistant_hook is None:
                host.speak(TTS_PREFIX + "Home Assistant is not set up.")
            else:
                self.homeassistant_hook(action.argument, source, action.pattern)
        elif action.action == "help":
            text = help_text(host.controller.list_macros(), host.controller.config, action.argument, host.held_reason)
            host.speak(TTS_PREFIX + text)
        elif action.action == "metar":
            if self.metar_hook is None:
                host.speak(TTS_PREFIX + "Airport weather is not set up.")
            else:
                self.metar_hook(action.argument)
        elif action.action == "parrot":
            if host.audio_output is None:
                _logger.warning("parrot needs live audio, which isn't running")
            elif host.audio_output.arm_parrot():
                host.speak(TTS_PREFIX + "Parrot ready. Key up and speak.")
        else:
            _logger.warning("DTMF action %s(%r) did nothing", action.action, action.argument)

    def speak(self, clip: str) -> None:
        """Queue `clip` like an announcement, rendering it off the event loop
        first so the controller knows how long to hold PTT."""
        host = self._host
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if host.renderer is None or loop is None:
            self.queue_announcement(clip)
            return
        config = host.controller.config

        def rendered(future: "asyncio.Future") -> None:
            if future.exception() is not None:
                _logger.error("couldn't render %s: %s", clip, future.exception())
            elif not self.queue_announcement(clip):
                _logger.warning("announcement queue full; dropped %s", clip)

        loop.run_in_executor(None, host.renderer.render, clip, config).add_done_callback(rendered)

    def list_macros(self) -> list[Macro]:
        return self._host.controller.list_macros()

    def add_macro(self, macro: Macro) -> list[Macro]:
        _logger.info("add_macro(pattern=%r, command=%r)", macro.pattern, macro.command)
        host = self._host
        macros = [m for m in host.controller.list_macros() if m.pattern != macro.pattern]
        macros.append(macro)
        host.controller.set_macros(macros)
        host._persist()
        return host.controller.list_macros()

    def delete_macro(self, pattern: str) -> list[Macro]:
        _logger.info("delete_macro(pattern=%r)", pattern)
        host = self._host
        macros = [m for m in host.controller.list_macros() if m.pattern != pattern]
        host.controller.set_macros(macros)
        host._persist()
        return macros

    def list_announcements(self) -> list[Announcement]:
        return self.scheduler.list()

    def next_announcement_run(self, announcement_id: str) -> Optional[datetime]:
        return self.scheduler.next_run(announcement_id)

    def save_announcement(self, announcement: Announcement) -> Announcement:
        _logger.info("save_announcement(id=%r, name=%r)", announcement.id, announcement.name)
        self.scheduler.upsert(announcement, self._host.wall_now())
        self._host._persist()
        return announcement

    def delete_announcement(self, announcement_id: str) -> None:
        _logger.info("delete_announcement(id=%r)", announcement_id)
        self.scheduler.remove(announcement_id, self._host.wall_now())
        self._host._persist()

    def announcement_clip(self, announcement: Announcement) -> str:
        """The controller clip for an announcement: its uploaded recording,
        or its message through text-to-speech."""
        if announcement.asset_id:
            return ASSET_PREFIX + announcement.asset_id
        return TTS_PREFIX + announcement.message

    def due_announcement_clips(self) -> list[str]:
        """Scheduled announcements to play now. During a net they wait
        (if net_hold_announcements), and play once it's over."""
        host = self._host
        due = self.scheduler.due(host.wall_now())
        for announcement in due:
            _logger.info("announcement %r is due", announcement.name)
        clips = [self.announcement_clip(a) for a in due]
        if host.net_active and host.saved_config.net_hold_announcements:
            self._held_announcements = (self._held_announcements + clips)[-MAX_HELD_ANNOUNCEMENTS:]
            return []
        held, self._held_announcements = self._held_announcements, []
        return held + clips

    def queue_announcement(self, clip: str) -> bool:
        """Callers should render `clip` first (off the event loop) so the
        controller knows how long to hold PTT for."""
        return self._host.controller.queue_announcement(clip)
