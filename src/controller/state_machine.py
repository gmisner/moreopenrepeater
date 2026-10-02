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
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

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
ANNOUNCEMENT_FALLBACK_DURATION = 10.0

_logger = logging.getLogger("moreopenrepeater.controller")


@dataclass(frozen=True)
class RepeaterConfig:
    courtesy_tone_duration: float = 0.2
    hang_time: float = 3.0
    tot_duration: float = 180.0
    id_interval: float = 600.0
    # Off: ID only while there's been activity since the last one. On: ID every
    # interval around the clock, like a beacon.
    idle_id: bool = False
    id_audio_duration: float = 3.0
    require_ctcss_hz: Optional[float] = None
    kerchunk_delay: float = 0.0  # carrier must last this long before keying up from idle; 0 = off
    # Stuck-carrier lockout: this many timeouts within lockout_window (a
    # carrier that never drops times out again every tot_duration) stops all
    # repeating until the channel has been quiet for lockout_clear_after.
    # IDs still go out. 0 = off.
    lockout_timeouts: int = 3
    lockout_window: float = 900.0
    lockout_clear_after: float = 60.0
    # Off: nothing transmits (no repeat, IDs or announcements), but DTMF is
    # still decoded so a control operator can turn it back on over the air.
    transmitter_enabled: bool = True
    callsign: str = ""
    id_mode: Literal["voice", "cw", "both"] = "voice"
    cw_wpm: float = 20.0
    cw_tone_hz: float = 700.0
    courtesy_tone_asset_id: Optional[str] = None
    id_asset_id: Optional[str] = None
    timeout_tone_asset_id: Optional[str] = None
    courtesy_tone_style: Literal["beep", "high_low", "low_high", "triple", "chirp"] = "beep"
    # Different tones after a linked station (AllStarLink/EchoLink) and at the
    # end of a phone call, so listeners can tell; "same" uses the one above.
    courtesy_tone_link_style: Literal["same", "beep", "high_low", "low_high", "triple", "chirp"] = "same"
    courtesy_tone_link_asset_id: Optional[str] = None
    courtesy_tone_patch_style: Literal["same", "beep", "high_low", "low_high", "triple", "chirp"] = "same"
    courtesy_tone_patch_asset_id: Optional[str] = None
    voice_id_text: str = "{callsign} repeater"
    id_phonetic: bool = False
    tts_voice: str = ""
    aprs_enabled: bool = False
    aprs_server: str = "rotate.aprs2.net"
    aprs_port: int = 14580
    aprs_callsign: str = ""
    aprs_lat: Optional[float] = None
    aprs_lon: Optional[float] = None
    aprs_comment: str = ""
    aprs_beacon_interval: float = 1800.0
    aprs_symbol: str = "/r"  # symbol table + code: "/r" repeater, "/#" digipeater, "/-" house
    # Beacon comment prefix like "146.940MHz T100 -060" so radios can tune to us.
    aprs_frequency_mhz: Optional[float] = None
    aprs_offset_mhz: Optional[float] = None
    aprs_tone_hz: Optional[float] = None  # None = use require_ctcss_hz
    aprs_map_enabled: bool = False  # receive nearby stations from APRS-IS for the map
    aprs_map_radius_km: float = 50.0
    aprs_map_hours: float = 3.0  # how long stations stay on the map
    # Map background; "" = none (no internet). Any {z}/{x}/{y} tile server works.
    aprs_map_tiles: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    distance_units: Literal["mi", "km"] = "mi"
    wx_alerts_enabled: bool = False
    wx_lat: Optional[float] = None
    wx_lon: Optional[float] = None
    wx_min_severity: Literal["Minor", "Moderate", "Severe", "Extreme"] = "Severe"
    wx_poll_interval: float = 120.0
    wx_repeat_minutes: float = 0.0  # 0 = announce each alert once
    audio_enabled: bool = False
    audio_input_device: str = ""  # PortAudio device name; "" = system default
    audio_output_device: str = ""
    cos_source: Literal["vox", "ctcss", "cm108", "gpio"] = "vox"  # "gpio" = a Raspberry Pi header pin
    cos_polarity: Literal["low", "high"] = "low"  # hardware COS (CM108 or Pi pin): the level meaning "carrier"
    cos_gpio_pin: int = 27  # BCM numbers; GPIO27 is header pin 13
    ptt_output: Literal["cm108", "gpio"] = "cm108"  # "cm108" keys a CM108 interface's PTT, if one is set up
    ptt_gpio_pin: int = 17  # header pin 11
    ptt_polarity: Literal["high", "low"] = "high"  # the level that keys the transmitter
    vox_threshold_db: float = -40.0
    vox_hold: float = 0.4
    tx_gain_db: float = 0.0
    tx_ctcss_hz: Optional[float] = None  # sub-audible tone added to everything transmitted
    tx_ctcss_level_db: float = -20.0
    record_transmissions: bool = False
    recording_retention_days: float = 7.0
    autopatch_enabled: bool = False
    autopatch_access_code: str = "*6"
    autopatch_hangup_code: str = "#"
    # Asterisk channel to call; {number} is replaced with the dialed digits.
    autopatch_dial_string: str = "PJSIP/{number}@mor-trunk"  # the trunk the dashboard sets up
    autopatch_ten_digit_prefix: str = ""  # "", "1" or "+1"; see controller.autopatch.format_number
    autopatch_caller_id: str = ""
    autopatch_allowed: str = "911 NXXNXXXXXX"  # dialplan patterns, see controller.autopatch
    autopatch_blocked: str = "900XXXXXXX NXX976XXXX"
    autopatch_max_call_seconds: float = 180.0
    autopatch_ring_seconds: float = 30.0
    autopatch_incoming_enabled: bool = False  # answer calls to the phone line
    autopatch_incoming_pin: str = ""  # callers key it in; calls aren't answered without one
    # Voice mailbox (controller.mailbox, api.mailbox).
    mailbox_enabled: bool = False
    mailbox_leave_code: str = "*7"
    mailbox_play_code: str = "*8"
    mailbox_delete_code: str = "*9"
    mailbox_max_seconds: float = 60.0
    mailbox_retention_days: float = 14.0
    mailbox_reminder_minutes: float = 60.0  # "messages waiting for mailbox 12"; 0 = never
    backup_enabled: bool = False  # scheduled backups to the backup folder
    backup_interval_hours: float = 24.0
    backup_keep: int = 7
    backup_include_recordings: bool = False
    # Spare CM108 GPIO pins by number ("1", "2", "4"-"8"): {"mode": "output" | "input", "name": str}.
    # Inputs can also have "invert" and "on_say"/"off_say"/"on_macro"/"off_macro".
    gpio_pins: dict = field(default_factory=dict)
    gpio_schedules: list = field(default_factory=list)  # [{pin, days, time, minutes, enabled}], outputs on for a weekly window
    # Nodes to connect to with one click: [{"node": str, "name": str, "monitor": bool}]
    link_favorites: list = field(default_factory=list)
    link_schedules: list = field(default_factory=list)  # [{node, name, days, time, minutes, monitor, enabled}]
    # GMRS (47 CFR Part 95 Subpart E) instead of amateur rules: IDs at least
    # every 15 minutes, and no phone patch, linking or APRS. See controller.modes.
    gmrs_mode: bool = False
    # Net mode: these apply while a net is running (see controller.modes).
    net_name: str = "Net"
    net_tot_duration: float = 600.0
    net_hang_time: float = 1.0
    net_courtesy_tone_style: Literal["same", "beep", "high_low", "low_high", "triple", "chirp"] = "same"
    net_courtesy_tone_asset_id: Optional[str] = None
    net_hold_autopatch: bool = True
    net_hold_announcements: bool = True  # scheduled ones play after the net; weather alerts never wait
    net_links: Literal["leave", "disconnect", "connect"] = "leave"
    net_link_node: str = ""  # with net_links "connect": linked for the net, dropped after
    net_start_say: str = ""  # said when the net starts / ends; "" = nothing
    net_end_say: str = ""
    net_max_minutes: float = 120.0  # a net left running ends by itself
    net_schedules: list = field(default_factory=list)  # [{days, time, minutes, enabled}]
    homeassistant_url: str = ""  # e.g. http://homeassistant.local:8123, for `homeassistant` macros
    homeassistant_say_result: bool = True  # "Done." / "Failed." after a Home Assistant macro
    # The listening page at /listen (api.app): off, for signed-in accounts
    # only (including listener accounts), or for anyone.
    public_page_mode: Literal["off", "signed_in", "anyone"] = "off"
    public_page_text: str = ""  # e.g. the frequency and tone
    public_page_audio: bool = True  # let visitors listen live
    public_page_max_listeners: int = 20


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
        self._tot_deadline: Optional[float] = None
        self._state_deadline: Optional[float] = None
        self._id_due_at = now + config.id_interval
        self._id_owed = False  # transmitted since the last ID
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
        """A local user is transmitting (carrier with the right CTCSS tone)."""
        return self._local_carrier and self._ctcss_present

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
        if config.require_ctcss_hz != previous.require_ctcss_hz:
            self._ctcss_present = config.require_ctcss_hz is None or self._last_ctcss_hz == config.require_ctcss_hz
        commands: list[ControllerCommand] = []
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
            self._last_ctcss_hz = event.tone_hz
            self._ctcss_present = (
                self.config.require_ctcss_hz is None or event.tone_hz == self.config.require_ctcss_hz
            )
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

        if self.carrier_present or self._remote_keyed:
            self._quiet_since = None
        elif self._quiet_since is None:
            self._quiet_since = now
        return commands

    def quiet_for(self, now: float) -> float:
        return 0.0 if self._quiet_since is None else now - self._quiet_since

    def tick(self, now: float) -> list[ControllerCommand]:
        commands: list[ControllerCommand] = []

        if self._keyup_at is not None and now >= self._keyup_at:
            self._keyup_at = None
            if self.state == IDLE and self._ctcss_present:
                commands += self._enter_receiving(now)

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
                commands += self._enter_hang_time(now)
            elif self.state == HANG_TIME:
                commands += self._enter_idle(now)
            elif self.state == TRANSMITTING_ID:
                commands += self._exit_id(now)
            elif self.state == ANNOUNCING:
                commands += self._finish_announcement(now)

        self._dtmf.tick(now)
        self._patch_dialer.tick(now)
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
            if not remote and not self._ctcss_present:
                return []  # squelch open but wrong/no CTCSS -- ignore
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
        if (self._local_carrier and self._ctcss_present) if remote else self._remote_keyed:
            return []  # the other side is still talking
        if self.state == RECEIVING:
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

    def _enter_receiving(self, now: float) -> list[ControllerCommand]:
        self._note_transmission(now)
        self._set_state(RECEIVING)
        self._tot_deadline = now + self.config.tot_duration
        self._state_deadline = None
        return [AssertPTT(active=True)]

    def _enter_courtesy_tone(self, now: float, clip: str) -> list[ControllerCommand]:
        """`clip` says who unkeyed last: "courtesy_tone" (a local user),
        "courtesy_tone_link" or "courtesy_tone_patch"."""
        self._set_state(COURTESY_TONE)
        self._tot_deadline = None
        self._state_deadline = now + self.config.courtesy_tone_duration
        return [PlayAudio(clip=clip)]

    def _enter_hang_time(self, now: float) -> list[ControllerCommand]:
        self._set_state(HANG_TIME)
        self._state_deadline = now + self.config.hang_time
        return []

    def _enter_idle(self, now: float) -> list[ControllerCommand]:
        self._set_state(LOCKOUT if self._locked_out else IDLE)
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
        self._state_deadline = now + (self._clip_duration("id") or self.config.id_audio_duration)
        self._id_due_at = now + self.config.id_interval
        self._id_owed = False
        return [AssertPTT(active=True), PlayAudio(clip="id")]

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
