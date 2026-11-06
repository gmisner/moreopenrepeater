"""Event-driven repeater state machine.

Callers feed real-world events via handle_event() and advance internal
timers via tick(now) -- there are no threads and no real time.sleep, so the
whole thing can be driven deterministically from tests with a fake clock.

Implemented as an explicit hand-rolled state machine rather than a generic
FSM library: with nine states and a handful of transitions, an explicit
dispatch is easier to read, easier to test exhaustively, and avoids an
extra dependency for logic this size.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from controller.config import RepeaterConfig

from .events import (
    AssertPTT,
    COSChanged,
    CTCSSChanged,
    ControllerCommand,
    ControllerEvent,
    DTMFDigit,
    LinkStateChanged,
    PlayAudio,
    RemoteKeyed,
    ToneBurst,
)
from .autopatch import AutopatchDialer
from .mailbox import MailboxDialer
from .macros import DTMFCommandDecoder, Macro

IDLE = "idle"
RECEIVING = "receiving"
COURTESY_TONE = "courtesy_tone"
HANG_TIME = "hang_time"
TIMEOUT = "timeout"
TRANSMITTING_ID = "transmitting_id"
ANNOUNCING = "announcing"
PATCH = "patch"  # autopatch call: transmitter held up carrying the phone audio
LOCKOUT = "lockout"  # stuck carrier: nothing is repeated until the channel goes quiet

MAX_QUEUED_ANNOUNCEMENTS = 10
TONE_BURST_GRACE = 5.0  # seconds a 1750 Hz burst heard without a carrier still opens the repeater
ANNOUNCEMENT_FALLBACK_DURATION = 10.0

_logger = logging.getLogger("moreopenrepeater.controller")


class RepeaterController:
    def __init__(
        self,
        config: RepeaterConfig,
        macros: Optional[list[Macro]] = None,
        now: float = 0.0,
        clip_duration: Callable[[str], Optional[float]] = lambda clip: None,
    ) -> None:
        """`clip_duration` reports how long a clip really plays when the
        playout layer knows (e.g. a rendered voice ID); the ID state lasts
        that long instead of the configured `id_audio_duration` guess."""
        self.config = config
        self._clip_duration = clip_duration
        self.state = IDLE
        self._dtmf = DTMFCommandDecoder(macros or [])
        self._patch_dialer = AutopatchDialer()
        self._mailbox_dialer = MailboxDialer()
        self._local_carrier = False
        self._ctcss_present = config.require_ctcss_hz is None
        self._last_ctcss_hz: Optional[float] = None
        self._up = False  # brought up by a local user or a link, and not yet back to idle
        self._burst_until: Optional[float] = None  # a 1750 Hz burst grants access until then
        self._tot_deadline: Optional[float] = None
        self._state_deadline: Optional[float] = None
        self._id_due_at = now + config.id_interval
        self._id_owed = False  # transmitted since the last ID
        self._long_id_due_at = now + config.long_id_interval
        self._pending_tx_since: Optional[float] = None  # a transmission not yet long enough to make an ID owed
        self._receiving_ptt = False  # the transmitter is up in RECEIVING (always, except on a simplex node)
        self._resume_state_after_id = IDLE
        self._remote_keyed: set[str] = set()
        self._announcements: list[str] = []
        self._keyup_at: Optional[float] = None  # carrier seen while idle; keys up then if it lasts
        self.kerchunks_filtered = 0
        self._timeouts: list[float] = []  # recent timeouts, for the lockout
        self._locked_out = False
        self._quiet_since: Optional[float] = now  # nobody (local or linked) has been keyed since
        self.lockouts = 0

    # -- public API -------------------------------------------------------

    def list_macros(self) -> list[Macro]:
        return self._dtmf.list_macros()

    def set_macros(self, macros: list[Macro]) -> None:
        self._dtmf.set_macros(macros)

    def queue_announcement(self, clip: str) -> bool:
        """Play `clip` the next time the channel is idle. Announcements never
        interrupt a user or the ID; they wait their turn. Returns False (and
        drops the clip) if too many are already waiting."""
        if len(self._announcements) >= MAX_QUEUED_ANNOUNCEMENTS:
            _logger.warning("announcement queue full; dropping %s", clip)
            return False
        self._announcements.append(clip)
        return True

    @property
    def queued_announcements(self) -> list[str]:
        return list(self._announcements)

    @property
    def carrier_present(self) -> bool:
        """A local user is transmitting, with whatever access the mode needs."""
        return self._local_carrier and self._access_ok

    @property
    def _access_ok(self) -> bool:
        mode = self.config.access_mode
        if mode == "carrier":
            return self._ctcss_present
        if self._up:
            return True
        if mode == "ctcss_open":
            return self._ctcss_present
        return self._burst_until is not None

    @property
    def locked_out(self) -> bool:
        return self._locked_out

    def clear_lockout(self, now: float) -> list[ControllerCommand]:
        """End a stuck-carrier lockout early (it also clears by itself once
        the channel has been quiet for `lockout_clear_after`)."""
        if not self._locked_out:
            return []
        self._locked_out = False
        self._timeouts = []
        _logger.info("stuck-carrier lockout cleared")
        if self.state == TRANSMITTING_ID and self._resume_state_after_id == LOCKOUT:
            self._resume_state_after_id = IDLE
            return []
        if self.state != LOCKOUT:
            return []
        self._set_state(IDLE)
        if self.config.transmitter_enabled and (self.carrier_present or self._remote_keyed):
            return self._enter_receiving(now)
        return []

    def set_patch_call_active(self, active: bool) -> None:
        """While a call is ringing or up, the hangup code ends it and the
        access code doesn't start another."""
        self._patch_dialer.call_active = active
        self._patch_dialer.reset()

    def start_patch(self, now: float) -> list[ControllerCommand]:
        """Hold the transmitter up for an autopatch call until `end_patch`.
        Local users are still repeated over the call audio."""
        if not self.config.transmitter_enabled:
            return []
        self._keyup_at = None
        self._note_transmission(now)
        if self.state == TRANSMITTING_ID:
            self._resume_state_after_id = PATCH
            return []
        self._set_state(PATCH)
        self._state_deadline = None
        self._tot_deadline = None
        return [AssertPTT(active=True)]

    def end_patch(self, now: float) -> list[ControllerCommand]:
        if self.state == TRANSMITTING_ID and self._resume_state_after_id == PATCH:
            self._resume_state_after_id = IDLE
            return []
        if self.state != PATCH:
            return []
        if self._locked_out:
            return self._enter_idle(now)
        if self.carrier_present:
            return self._enter_receiving(now)
        return self._enter_courtesy_tone(now, "courtesy_tone_patch")

    def update_config(self, config: RepeaterConfig, now: float) -> list[ControllerCommand]:
        """Apply new settings to derived state too, so an edit takes effect
        now rather than at the next ID or the next CTCSS change."""
        previous = self.config
        self.config = config
        if config.id_interval != previous.id_interval:
            self._id_due_at = now + config.id_interval
        if config.long_id_interval != previous.long_id_interval:
            self._long_id_due_at = now + config.long_id_interval
        if config.require_ctcss_hz != previous.require_ctcss_hz:
            self._ctcss_present = config.require_ctcss_hz is None or self._last_ctcss_hz == config.require_ctcss_hz
        commands: list[ControllerCommand] = self._sync_receiving_ptt(now)
        if config.access_mode != previous.access_mode:
            self._burst_until = None
        if config.lockout_timeouts <= 0:
            commands += self.clear_lockout(now)
        if previous.transmitter_enabled and not config.transmitter_enabled:
            self._keyup_at = None
            if self.state not in (IDLE, LOCKOUT):
                commands += self._enter_idle(now)
        return commands

    def _mailbox_codes(self) -> dict[str, str]:
        config = self.config
        return {"leave": config.mailbox_leave_code, "play": config.mailbox_play_code, "delete": config.mailbox_delete_code}

    def handle_event(self, event: ControllerEvent, now: float) -> list[ControllerCommand]:
        commands: list[ControllerCommand] = []

        if isinstance(event, COSChanged):
            self._local_carrier = event.active
            commands += self._on_cos_changed(event.active, now)
            if not event.active and self.config.autopatch_enabled:
                patch_command = self._patch_dialer.carrier_dropped(self.config.autopatch_access_code)
                if patch_command is not None:
                    commands.append(patch_command)
            if not event.active and self.config.mailbox_enabled:
                mailbox_command = self._mailbox_dialer.carrier_dropped(self._mailbox_codes())
                if mailbox_command is not None:
                    commands.append(mailbox_command)
        elif isinstance(event, CTCSSChanged):
            had_access = self._access_ok
            self._last_ctcss_hz = event.tone_hz
            self._ctcss_present = (
                self.config.require_ctcss_hz is None or event.tone_hz == self.config.require_ctcss_hz
            )
            commands += self._access_gained(had_access, now)
        elif isinstance(event, ToneBurst):
            if self.config.access_mode == "tone_burst":
                had_access = self._access_ok
                self._burst_until = now + TONE_BURST_GRACE
                commands += self._access_gained(had_access, now)
        elif isinstance(event, DTMFDigit):
            command = self._dtmf.handle_digit(event.digit, now)
            if command is not None:
                commands.append(command)
            if self.config.autopatch_enabled:
                patch_command = self._patch_dialer.handle_digit(
                    event.digit, now, self.config.autopatch_access_code, self.config.autopatch_hangup_code
                )
                if patch_command is not None:
                    commands.append(patch_command)
            if self.config.mailbox_enabled:
                mailbox_command = self._mailbox_dialer.handle_digit(event.digit, now, self._mailbox_codes())
                if mailbox_command is not None:
                    commands.append(mailbox_command)
        elif isinstance(event, RemoteKeyed):
            if event.keyed:
                self._remote_keyed.add(event.node_id)
                commands += self._on_cos_changed(True, now, remote=True)
            else:
                self._remote_keyed.discard(event.node_id)
                if not self._remote_keyed:
                    commands += self._on_cos_changed(False, now, remote=True)
        elif isinstance(event, LinkStateChanged):
            pass  # tracked by the link layer; no local repeater-state effect yet
        if isinstance(event, (COSChanged, RemoteKeyed, CTCSSChanged, ToneBurst)):
            commands += self._sync_receiving_ptt(now)

        if self.carrier_present or self._remote_keyed:
            self._quiet_since = None
        elif self._quiet_since is None:
            self._quiet_since = now
        return commands

    def _access_gained(self, had_access: bool, now: float) -> list[ControllerCommand]:
        """The tone (or burst) arrived after the carrier, as it does when
        detecting it takes longer than the squelch opening: open now."""
        if self._local_carrier and self._access_ok and not had_access:
            return self._on_cos_changed(True, now)
        return []

    def quiet_for(self, now: float) -> float:
        return 0.0 if self._quiet_since is None else now - self._quiet_since

    def tick(self, now: float) -> list[ControllerCommand]:
        commands: list[ControllerCommand] = []

        if self._burst_until is not None and now > self._burst_until and not self._up:
            self._burst_until = None
        if self._keyup_at is not None and now >= self._keyup_at:
            self._keyup_at = None
            if self.state == IDLE and self._access_ok:
                commands += self._enter_receiving(now)

        if self._pending_tx_since is not None:
            if self.state not in (RECEIVING, TIMEOUT):
                self._pending_tx_since = None
            elif now - self._pending_tx_since >= self.config.id_skip_short_seconds:
                self._note_transmission(self._pending_tx_since)
                self._pending_tx_since = None

        if self._tot_deadline is not None and now >= self._tot_deadline:
            if self.state == RECEIVING:
                commands += self._enter_timeout(now)
            elif self.state == TIMEOUT:
                self._tot_deadline = now + self.config.tot_duration
                commands += self._count_timeout(now)

        if (
            self._locked_out
            and self._quiet_since is not None
            and now - self._quiet_since >= self.config.lockout_clear_after
        ):
            commands += self.clear_lockout(now)

        if self._state_deadline is not None and now >= self._state_deadline:
            if self.state == COURTESY_TONE:
                commands += self._end_simplex_courtesy_tone(now) if self._simplex else self._end_courtesy_tone(now)
            elif self.state == HANG_TIME:
                commands += self._enter_idle(now)
            elif self.state == TRANSMITTING_ID:
                commands += self._exit_id(now)
            elif self.state == ANNOUNCING:
                commands += self._finish_announcement(now)

        self._dtmf.tick(now)
        patch_command = self._patch_dialer.tick(now, self.config.autopatch_access_code)
        if patch_command is not None and self.config.autopatch_enabled:
            commands.append(patch_command)
        self._mailbox_dialer.tick(now)

        if not self.config.transmitter_enabled:
            return commands

        if self.state in (IDLE, HANG_TIME, PATCH, LOCKOUT) and self._id_due(now):
            commands += self._enter_id(now)

        if self.state == IDLE and self._announcements and self._keyup_at is None:
            commands += self._start_announcement(now)

        return commands

    # -- internal transitions ---------------------------------------------

    def _on_cos_changed(self, active: bool, now: float, remote: bool = False) -> list[ControllerCommand]:
        if active:
            if not self.config.transmitter_enabled:
                return []
            if not remote and not self._access_ok:
                return []  # squelch open without the CTCSS tone or burst the access mode needs -- ignore
            if self.state == IDLE and not remote and self.config.kerchunk_delay > 0:
                self._keyup_at = now + self.config.kerchunk_delay
                return []
            if self.state in (IDLE, HANG_TIME):
                return self._enter_receiving(now)
            return []

        if self._keyup_at is not None and not remote:
            self._keyup_at = None
            self.kerchunks_filtered += 1
            _logger.info("kerchunk ignored (carrier shorter than %.2fs)", self.config.kerchunk_delay)
            return []
        if self.carrier_present if remote else self._remote_keyed:
            return []  # the other side is still talking
        if self.state == RECEIVING:
            if self._pending_tx_since is not None:
                if now - self._pending_tx_since >= self.config.id_skip_short_seconds:
                    self._note_transmission(self._pending_tx_since)
                self._pending_tx_since = None
            if self._tot_deadline is not None and now >= self._tot_deadline:
                commands = self._enter_timeout(now)
                # Already unkeyed, so there's nothing left to wait for.
                return commands + (self._enter_idle(now) if self.state == TIMEOUT else [])
            return self._enter_courtesy_tone(now, "courtesy_tone_link" if remote else "courtesy_tone")
        if self.state == TIMEOUT:
            return self._enter_idle(now)
        return []

    def _set_state(self, new_state: str) -> None:
        if new_state != self.state:
            _logger.info("state %s -> %s", self.state, new_state)
        self.state = new_state

    def _id_due(self, now: float) -> bool:
        return (self._id_owed or self.config.idle_id) and now >= self._id_due_at

    def _note_transmission(self, now: float) -> None:
        """The first transmission after an ID starts the countdown to the
        next one, so IDs come every interval while the repeater is in use and
        once more after the last of it (§97.119), and not at all when idle."""
        if not self._id_owed and not self.config.idle_id:
            self._id_due_at = now + self.config.id_interval
        self._id_owed = True

    @property
    def _simplex(self) -> bool:
        return self.config.node_mode == "simplex"

    def _receive_ptt(self) -> bool:
        """Whether to transmit while RECEIVING. A simplex node shares one
        frequency with its local users: it sends linked stations, but never
        over anyone on the channel (with or without the right CTCSS tone)."""
        if not self._simplex:
            return True
        return bool(self._remote_keyed) and not self._local_carrier

    def _sync_receiving_ptt(self, now: float) -> list[ControllerCommand]:
        if self.state != RECEIVING or self._receive_ptt() == self._receiving_ptt:
            return []
        self._receiving_ptt = not self._receiving_ptt
        if self._receiving_ptt:
            self._note_transmission(now)
        return [AssertPTT(active=self._receiving_ptt)]

    def _enter_receiving(self, now: float) -> list[ControllerCommand]:
        self._receiving_ptt = self._receive_ptt()
        if not self._receiving_ptt:
            pass  # a local user on a simplex node: nothing is transmitted, so no ID is owed for it
        elif self.config.id_skip_short_seconds > 0 and not self._id_owed and not self._simplex:
            self._pending_tx_since = now  # counts once it's been on the air long enough
        else:
            self._note_transmission(now)
        self._set_state(RECEIVING)
        self._up = True
        self._burst_until = None
        self._tot_deadline = now + self.config.tot_duration
        self._state_deadline = None
        return [AssertPTT(active=self._receiving_ptt)]

    def _end_simplex_courtesy_tone(self, now: float) -> list[ControllerCommand]:
        """No hang time on a simplex node: holding the transmitter up would
        keep the next local user from being heard."""
        commands = self._enter_idle(now)
        if self.config.transmitter_enabled and (self.carrier_present or self._remote_keyed):
            commands += self._enter_receiving(now)
        return commands

    def _end_courtesy_tone(self, now: float) -> list[ControllerCommand]:
        """A key-up while the tone played was ignored so the tone could
        finish; pick it up now instead of hanging and dropping it."""
        if self.config.transmitter_enabled and (self.carrier_present or self._remote_keyed):
            return self._enter_receiving(now)
        return self._enter_hang_time(now)

    def _courtesy_sound(self, clip: str) -> tuple[str, Optional[str]]:
        """(built-in style, uploaded clip id) for a courtesy clip name."""
        source = {"courtesy_tone_link": "link", "courtesy_tone_patch": "patch"}.get(clip)
        if source:
            style = getattr(self.config, f"courtesy_tone_{source}_style")
            asset = getattr(self.config, f"courtesy_tone_{source}_asset_id")
            if asset or style != "same":
                return (self.config.courtesy_tone_style if style == "same" else style), asset
        return self.config.courtesy_tone_style, self.config.courtesy_tone_asset_id

    def _courtesy_off(self, clip: str, duration: Optional[float]) -> bool:
        """True when this courtesy clip should not play (style Off, or a
        rendered empty clip). An uploaded clip is ignored when style is Off."""
        if duration is not None and duration <= 0:
            return True
        style, _asset = self._courtesy_sound(clip)
        return style == "none"

    def _enter_courtesy_tone(self, now: float, clip: str) -> list[ControllerCommand]:
        """`clip` says who unkeyed last: "courtesy_tone" (a local user),
        "courtesy_tone_link" or "courtesy_tone_patch"."""
        if self._simplex:
            if clip == "courtesy_tone" and not self.config.simplex_courtesy_tone:
                return self._enter_idle(now)
        duration = self._clip_duration(clip)
        if self._courtesy_off(clip, duration):
            return self._enter_idle(now) if self._simplex else self._enter_hang_time(now)
        if self._simplex:
            self._note_transmission(now)  # the tone keys the transmitter itself
        self._set_state(COURTESY_TONE)
        self._tot_deadline = None
        self._state_deadline = now + (duration if duration is not None else self.config.courtesy_tone_duration)
        return [PlayAudio(clip=clip)]

    def _enter_hang_time(self, now: float) -> list[ControllerCommand]:
        self._set_state(HANG_TIME)
        self._state_deadline = now + self.config.hang_time
        return []

    def _enter_idle(self, now: float) -> list[ControllerCommand]:
        self._set_state(LOCKOUT if self._locked_out else IDLE)
        self._up = False
        self._state_deadline = None
        self._tot_deadline = None
        return [AssertPTT(active=False)]

    def _enter_timeout(self, now: float) -> list[ControllerCommand]:
        self._set_state(TIMEOUT)
        self._tot_deadline = now + self.config.tot_duration  # a carrier that stays up times out again
        self._state_deadline = None
        return [PlayAudio(clip="timeout_tone"), AssertPTT(active=False)] + self._count_timeout(now)

    def _count_timeout(self, now: float) -> list[ControllerCommand]:
        limit = self.config.lockout_timeouts
        if limit <= 0 or self._locked_out:
            return []
        window_start = now - self.config.lockout_window
        self._timeouts = [t for t in self._timeouts if t > window_start] + [now]
        if len(self._timeouts) < limit:
            return []
        self._locked_out = True
        self._timeouts = []
        self.lockouts += 1
        self._up = False
        _logger.warning(
            "stuck-carrier lockout: %d timeouts in %.0f s; repeating stops until the channel is quiet for %.0f s",
            limit, self.config.lockout_window, self.config.lockout_clear_after,
        )
        self._set_state(LOCKOUT)
        self._tot_deadline = None
        return [AssertPTT(active=False)]

    def _enter_id(self, now: float) -> list[ControllerCommand]:
        self._keyup_at = None
        self._resume_state_after_id = self.state
        self._set_state(TRANSMITTING_ID)
        clip = "id"
        if self.config.long_id_mode != "off" and now >= self._long_id_due_at:
            clip = "id_long"
            self._long_id_due_at = now + self.config.long_id_interval
        self._state_deadline = now + (self._clip_duration(clip) or self.config.id_audio_duration)
        self._id_due_at = now + self.config.id_interval
        self._id_owed = False
        return [AssertPTT(active=True), PlayAudio(clip=clip)]

    def _exit_id(self, now: float) -> list[ControllerCommand]:
        target = self._resume_state_after_id
        self._set_state(target)
        if target in (IDLE, LOCKOUT):
            self._state_deadline = None
            return [AssertPTT(active=False)]
        if target == PATCH:
            self._id_owed = True  # the call is still on the air
            self._state_deadline = None
            return []
        self._state_deadline = now + self.config.hang_time  # resume hang_time countdown
        return []

    def _start_announcement(self, now: float) -> list[ControllerCommand]:
        self._note_transmission(now)
        commands: list[ControllerCommand] = [] if self.state == ANNOUNCING else [AssertPTT(active=True)]
        clip = self._announcements.pop(0)
        self._set_state(ANNOUNCING)
        self._state_deadline = now + (self._clip_duration(clip) or ANNOUNCEMENT_FALLBACK_DURATION)
        return commands + [PlayAudio(clip=clip)]

    def _finish_announcement(self, now: float) -> list[ControllerCommand]:
        if self._announcements and not self._id_due(now):
            return self._start_announcement(now)  # back-to-back, without dropping PTT
        return self._enter_idle(now)
