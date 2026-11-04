"""RepeaterConfig: flat public attrs composed from config facets.

Nested facet views (`config.weather`, …) project the same flat fields.
On-disk state and `/api/config` stay a flat JSON object — see ADR 0002.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, fields
from typing import Any, Literal, Mapping, Optional

from controller.config.facets import (
    IdentityTimingConfig,
    RadioAudioConfig,
    LinkRadioConfig,
    RemoteBaseConfig,
    AprsConfig,
    WeatherConfig,
    MonitorConfig,
    AutopatchConfig,
    MailboxConfig,
    NetConfig,
    GpioFanConfig,
    HomeassistantConfig,
    LinkingConfig,
    BackupConfig,
    TranscriptionConfig,
    PublicPageConfig,
)
from controller.config.types import CourtesyStyle, CourtesyVariant

FACET_FIELD_NAMES: dict[str, tuple[str, ...]] = {
    'identity_timing': ('courtesy_tone_duration', 'hang_time', 'tot_duration', 'id_interval', 'idle_id', 'node_mode', 'simplex_courtesy_tone', 'id_skip_short_seconds', 'long_id_mode', 'long_id_interval', 'long_id_text', 'long_id_asset_id', 'id_audio_duration', 'require_ctcss_hz', 'access_mode', 'tone_burst_ms', 'kerchunk_delay', 'lockout_timeouts', 'lockout_window', 'lockout_clear_after', 'transmitter_enabled', 'callsign', 'id_mode', 'cw_wpm', 'cw_tone_hz', 'cw_id_suffix', 'courtesy_tone_asset_id', 'id_asset_id', 'timeout_tone_asset_id', 'courtesy_tone_style', 'courtesy_tone_link_style', 'courtesy_tone_link_asset_id', 'courtesy_tone_patch_style', 'courtesy_tone_patch_asset_id', 'courtesy_tone_custom', 'voice_id_text', 'id_phonetic', 'tts_voice'),
    'radio_audio': ('rx_deemphasis', 'tx_preemphasis', 'audio_enabled', 'board_preset', 'setup_wizard_done', 'audio_input_device', 'audio_output_device', 'radio_model', 'radio_frequency', 'radio_notes', 'audio_input_levels', 'audio_output_levels', 'cos_source', 'cos_polarity', 'cos_gpio_pin', 'cos_serial_device', 'cos_serial_line', 'ptt_output', 'ptt_gpio_pin', 'ptt_serial_device', 'ptt_serial_line', 'ptt_polarity', 'vox_threshold_db', 'squelch_tail_ms', 'tx_delay_ms', 'dtmf_mute', 'vox_hold', 'vox_dtmf_hold', 'tx_gain_db', 'tx_ctcss_hz', 'tx_ctcss_level_db', 'record_transmissions', 'recording_retention_days'),
    'link_radio': ('link_radio_enabled', 'link_radio_name', 'link_radio_model', 'link_radio_frequency', 'link_radio_notes', 'link_radio_input_device', 'link_radio_output_device', 'link_radio_input_levels', 'link_radio_output_levels', 'link_radio_cos', 'link_radio_cos_polarity', 'link_radio_cos_gpio_pin', 'link_radio_cos_serial_device', 'link_radio_cos_serial_line', 'link_radio_vox_threshold_db', 'link_radio_ptt', 'link_radio_ptt_gpio_pin', 'link_radio_ptt_serial_device', 'link_radio_ptt_serial_line', 'link_radio_ptt_polarity', 'link_radio_tx_gain_db', 'link_radio_tx_ctcss_hz', 'link_radio_timeout', 'link_radio_squelch_tail_ms', 'link_radio_tx_delay_ms', 'link_radio_courtesy_tone', 'link_radio_mode'),
    'remote_base': ('remote_base_rigctld', 'remote_base_rig_mode', 'remote_base_mhz', 'remote_base_shift', 'remote_base_offset_mhz', 'remote_base_tone_hz', 'remote_base_ranges', 'remote_base_idle_minutes'),
    'aprs': ('aprs_enabled', 'aprs_server', 'aprs_port', 'aprs_callsign', 'aprs_lat', 'aprs_lon', 'aprs_comment', 'aprs_beacon_interval', 'aprs_symbol', 'aprs_frequency_mhz', 'aprs_offset_mhz', 'aprs_tone_hz', 'aprs_map_enabled', 'aprs_map_radius_km', 'aprs_map_hours', 'aprs_map_tiles', 'distance_units'),
    'weather': ('wx_alerts_enabled', 'wx_lat', 'wx_lon', 'wx_min_severity', 'wx_poll_interval', 'wx_repeat_minutes', 'metar_airports'),
    'monitor': ('monitor_enabled', 'monitor_name', 'monitor_input_device', 'monitor_squelch', 'monitor_vox_threshold_db', 'monitor_gpio_pin', 'monitor_gpio_polarity', 'monitor_gain_db', 'monitor_record'),
    'autopatch': ('autopatch_enabled', 'autopatch_access_code', 'autopatch_hangup_code', 'autopatch_dial_string', 'autopatch_ten_digit_prefix', 'autopatch_caller_id', 'autopatch_allowed', 'autopatch_blocked', 'autopatch_max_call_seconds', 'autopatch_ring_seconds', 'autopatch_incoming_enabled', 'autopatch_incoming_pin'),
    'mailbox': ('mailbox_enabled', 'mailbox_leave_code', 'mailbox_play_code', 'mailbox_delete_code', 'mailbox_max_seconds', 'mailbox_retention_days', 'mailbox_reminder_minutes'),
    'net': ('net_name', 'net_tot_duration', 'net_hang_time', 'net_courtesy_tone_style', 'net_courtesy_tone_asset_id', 'net_hold_autopatch', 'net_hold_announcements', 'net_links', 'net_link_node', 'net_start_say', 'net_end_say', 'net_max_minutes', 'net_schedules'),
    'gpio_fan': ('gpio_pins', 'gpio_schedules', 'fan_output', 'fan_cm108_pin', 'fan_gpio_pin', 'fan_polarity', 'fan_run_on_minutes', 'fan_temp_c'),
    'homeassistant': ('homeassistant_url', 'homeassistant_say_result'),
    'linking': ('link_favorites', 'link_schedules', 'gmrs_mode'),
    'backup': ('backup_enabled', 'backup_interval_hours', 'backup_keep', 'backup_include_recordings'),
    'transcription': ('transcription_engine', 'transcription_url', 'transcription_model'),
    'public_page': ('public_page_mode', 'public_page_text', 'public_page_audio', 'public_page_max_listeners'),
}

_FIELD_TO_FACET = {
    name: facet
    for facet, names in FACET_FIELD_NAMES.items()
    for name in names
}

@dataclass(frozen=True)
class RepeaterConfig:
    # --- identity_timing ---
    courtesy_tone_duration: float = 0.2
    hang_time: float = 3.0
    tot_duration: float = 180.0
    id_interval: float = 600.0
    idle_id: bool = False
    node_mode: Literal['repeater', 'simplex'] = 'repeater'
    simplex_courtesy_tone: bool = True
    id_skip_short_seconds: float = 0.0
    long_id_mode: Literal['off', 'voice', 'both'] = 'off'
    long_id_interval: float = 3600.0
    long_id_text: str = 'This is {callsign}. The time is {time}.'
    long_id_asset_id: Optional[str] = None
    id_audio_duration: float = 3.0
    require_ctcss_hz: Optional[float] = None
    access_mode: Literal['carrier', 'ctcss_open', 'tone_burst'] = 'carrier'
    tone_burst_ms: float = 300.0
    kerchunk_delay: float = 0.0
    lockout_timeouts: int = 3
    lockout_window: float = 900.0
    lockout_clear_after: float = 60.0
    transmitter_enabled: bool = True
    callsign: str = ''
    id_mode: Literal['voice', 'cw', 'both'] = 'voice'
    cw_wpm: float = 20.0
    cw_tone_hz: float = 700.0
    cw_id_suffix: str = ''
    courtesy_tone_asset_id: Optional[str] = None
    id_asset_id: Optional[str] = None
    timeout_tone_asset_id: Optional[str] = None
    courtesy_tone_style: CourtesyStyle = 'beep'
    courtesy_tone_link_style: CourtesyVariant = 'same'
    courtesy_tone_link_asset_id: Optional[str] = None
    courtesy_tone_patch_style: CourtesyVariant = 'same'
    courtesy_tone_patch_asset_id: Optional[str] = None
    courtesy_tone_custom: str = '880:100 0:40 1320:100'
    voice_id_text: str = '{callsign} repeater'
    id_phonetic: bool = False
    tts_voice: str = ''
    # --- radio_audio ---
    rx_deemphasis: bool = False
    tx_preemphasis: bool = False
    audio_enabled: bool = False
    board_preset: str = ''
    setup_wizard_done: bool = False
    audio_input_device: str = ''
    audio_output_device: str = ''
    radio_model: str = ''
    radio_frequency: str = ''
    radio_notes: str = ''
    audio_input_levels: dict = field(default_factory=dict)
    audio_output_levels: dict = field(default_factory=dict)
    cos_source: Literal['vox', 'ctcss', 'cm108', 'gpio', 'serial'] = 'vox'
    cos_polarity: Literal['low', 'high'] = 'low'
    cos_gpio_pin: int = 27
    cos_serial_device: str = ''
    cos_serial_line: Literal['cts', 'dsr', 'dcd'] = 'cts'
    ptt_output: Literal['cm108', 'gpio', 'serial'] = 'cm108'
    ptt_gpio_pin: int = 17
    ptt_serial_device: str = ''
    ptt_serial_line: Literal['rts', 'dtr'] = 'rts'
    ptt_polarity: Literal['high', 'low'] = 'high'
    vox_threshold_db: float = -40.0
    squelch_tail_ms: float = 0.0
    tx_delay_ms: float = 0.0
    dtmf_mute: bool = True
    vox_hold: float = 0.4
    vox_dtmf_hold: float = 3.0
    tx_gain_db: float = 0.0
    tx_ctcss_hz: Optional[float] = None
    tx_ctcss_level_db: float = -20.0
    record_transmissions: bool = False
    recording_retention_days: float = 7.0
    # --- link_radio ---
    link_radio_enabled: bool = False
    link_radio_name: str = ''
    link_radio_model: str = ''
    link_radio_frequency: str = ''
    link_radio_notes: str = ''
    link_radio_input_device: str = ''
    link_radio_output_device: str = ''
    link_radio_input_levels: dict = field(default_factory=dict)
    link_radio_output_levels: dict = field(default_factory=dict)
    link_radio_cos: Literal['vox', 'ctcss', 'cm108', 'gpio', 'serial'] = 'vox'
    link_radio_cos_polarity: Literal['low', 'high'] = 'low'
    link_radio_cos_gpio_pin: int = 23
    link_radio_cos_serial_device: str = ''
    link_radio_cos_serial_line: Literal['cts', 'dsr', 'dcd'] = 'cts'
    link_radio_vox_threshold_db: float = -40.0
    link_radio_ptt: Literal['cm108', 'gpio', 'serial', 'none'] = 'gpio'
    link_radio_ptt_gpio_pin: int = 24
    link_radio_ptt_serial_device: str = ''
    link_radio_ptt_serial_line: Literal['rts', 'dtr'] = 'rts'
    link_radio_ptt_polarity: Literal['high', 'low'] = 'high'
    link_radio_tx_gain_db: float = 0.0
    link_radio_tx_ctcss_hz: Optional[float] = None
    link_radio_timeout: float = 180.0
    link_radio_squelch_tail_ms: float = 0.0
    link_radio_tx_delay_ms: float = 0.0
    link_radio_courtesy_tone: bool = True
    link_radio_mode: Literal['link', 'remote_base'] = 'link'
    # --- remote_base ---
    remote_base_rigctld: str = 'localhost:4532'
    remote_base_rig_mode: Literal['FM', 'AM', 'USB', 'LSB'] = 'FM'
    remote_base_mhz: float = 146.52
    remote_base_shift: Literal['simplex', 'plus', 'minus'] = 'simplex'
    remote_base_offset_mhz: Optional[float] = None
    remote_base_tone_hz: Optional[float] = None
    remote_base_ranges: str = '144-148, 222-225, 420-450'
    remote_base_idle_minutes: float = 10.0
    # --- aprs ---
    aprs_enabled: bool = False
    aprs_server: str = 'rotate.aprs2.net'
    aprs_port: int = 14580
    aprs_callsign: str = ''
    aprs_lat: Optional[float] = None
    aprs_lon: Optional[float] = None
    aprs_comment: str = ''
    aprs_beacon_interval: float = 1800.0
    aprs_symbol: str = '/r'
    aprs_frequency_mhz: Optional[float] = None
    aprs_offset_mhz: Optional[float] = None
    aprs_tone_hz: Optional[float] = None
    aprs_map_enabled: bool = False
    aprs_map_radius_km: float = 50.0
    aprs_map_hours: float = 3.0
    aprs_map_tiles: str = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
    distance_units: Literal['mi', 'km'] = 'mi'
    # --- weather ---
    wx_alerts_enabled: bool = False
    wx_lat: Optional[float] = None
    wx_lon: Optional[float] = None
    wx_min_severity: Literal['Minor', 'Moderate', 'Severe', 'Extreme'] = 'Severe'
    wx_poll_interval: float = 120.0
    wx_repeat_minutes: float = 0.0
    metar_airports: list = field(default_factory=list)
    # --- monitor ---
    monitor_enabled: bool = False
    monitor_name: str = ''
    monitor_input_device: str = ''
    monitor_squelch: Literal['vox', 'gpio', 'open'] = 'vox'
    monitor_vox_threshold_db: float = -40.0
    monitor_gpio_pin: int = 22
    monitor_gpio_polarity: Literal['low', 'high'] = 'low'
    monitor_gain_db: float = 0.0
    monitor_record: bool = False
    # --- autopatch ---
    autopatch_enabled: bool = False
    autopatch_access_code: str = '*6'
    autopatch_hangup_code: str = '#'
    autopatch_dial_string: str = 'PJSIP/{number}@mor-trunk'
    autopatch_ten_digit_prefix: str = ''
    autopatch_caller_id: str = ''
    autopatch_allowed: str = '911 NXXNXXXXXX'
    autopatch_blocked: str = '900XXXXXXX NXX976XXXX'
    autopatch_max_call_seconds: float = 180.0
    autopatch_ring_seconds: float = 30.0
    autopatch_incoming_enabled: bool = False
    autopatch_incoming_pin: str = ''
    # --- mailbox ---
    mailbox_enabled: bool = False
    mailbox_leave_code: str = '*7'
    mailbox_play_code: str = '*8'
    mailbox_delete_code: str = '*9'
    mailbox_max_seconds: float = 60.0
    mailbox_retention_days: float = 14.0
    mailbox_reminder_minutes: float = 60.0
    # --- net ---
    net_name: str = 'Net'
    net_tot_duration: float = 600.0
    net_hang_time: float = 1.0
    net_courtesy_tone_style: CourtesyVariant = 'same'
    net_courtesy_tone_asset_id: Optional[str] = None
    net_hold_autopatch: bool = True
    net_hold_announcements: bool = True
    net_links: Literal['leave', 'disconnect', 'connect'] = 'leave'
    net_link_node: str = ''
    net_start_say: str = ''
    net_end_say: str = ''
    net_max_minutes: float = 120.0
    net_schedules: list = field(default_factory=list)
    # --- gpio_fan ---
    gpio_pins: dict = field(default_factory=dict)
    gpio_schedules: list = field(default_factory=list)
    fan_output: Literal['none', 'cm108', 'gpio'] = 'none'
    fan_cm108_pin: int = 1
    fan_gpio_pin: int = 26
    fan_polarity: Literal['high', 'low'] = 'high'
    fan_run_on_minutes: float = 3.0
    fan_temp_c: Optional[float] = None
    # --- homeassistant ---
    homeassistant_url: str = ''
    homeassistant_say_result: bool = True
    # --- linking ---
    link_favorites: list = field(default_factory=list)
    link_schedules: list = field(default_factory=list)
    gmrs_mode: bool = False
    # --- backup ---
    backup_enabled: bool = False
    backup_interval_hours: float = 24.0
    backup_keep: int = 7
    backup_include_recordings: bool = False
    # --- transcription ---
    transcription_engine: Literal['off', 'vosk', 'openai'] = 'off'
    transcription_url: str = 'https://api.openai.com/v1/audio/transcriptions'
    transcription_model: str = 'whisper-1'
    # --- public_page ---
    public_page_mode: Literal['off', 'signed_in', 'anyone'] = 'off'
    public_page_text: str = ''
    public_page_audio: bool = True
    public_page_max_listeners: int = 20

    @property
    def identity_timing(self) -> IdentityTimingConfig:
        return IdentityTimingConfig(courtesy_tone_duration=self.courtesy_tone_duration, hang_time=self.hang_time, tot_duration=self.tot_duration, id_interval=self.id_interval, idle_id=self.idle_id, node_mode=self.node_mode, simplex_courtesy_tone=self.simplex_courtesy_tone, id_skip_short_seconds=self.id_skip_short_seconds, long_id_mode=self.long_id_mode, long_id_interval=self.long_id_interval, long_id_text=self.long_id_text, long_id_asset_id=self.long_id_asset_id, id_audio_duration=self.id_audio_duration, require_ctcss_hz=self.require_ctcss_hz, access_mode=self.access_mode, tone_burst_ms=self.tone_burst_ms, kerchunk_delay=self.kerchunk_delay, lockout_timeouts=self.lockout_timeouts, lockout_window=self.lockout_window, lockout_clear_after=self.lockout_clear_after, transmitter_enabled=self.transmitter_enabled, callsign=self.callsign, id_mode=self.id_mode, cw_wpm=self.cw_wpm, cw_tone_hz=self.cw_tone_hz, cw_id_suffix=self.cw_id_suffix, courtesy_tone_asset_id=self.courtesy_tone_asset_id, id_asset_id=self.id_asset_id, timeout_tone_asset_id=self.timeout_tone_asset_id, courtesy_tone_style=self.courtesy_tone_style, courtesy_tone_link_style=self.courtesy_tone_link_style, courtesy_tone_link_asset_id=self.courtesy_tone_link_asset_id, courtesy_tone_patch_style=self.courtesy_tone_patch_style, courtesy_tone_patch_asset_id=self.courtesy_tone_patch_asset_id, courtesy_tone_custom=self.courtesy_tone_custom, voice_id_text=self.voice_id_text, id_phonetic=self.id_phonetic, tts_voice=self.tts_voice)

    @property
    def radio_audio(self) -> RadioAudioConfig:
        return RadioAudioConfig(rx_deemphasis=self.rx_deemphasis, tx_preemphasis=self.tx_preemphasis, audio_enabled=self.audio_enabled, board_preset=self.board_preset, setup_wizard_done=self.setup_wizard_done, audio_input_device=self.audio_input_device, audio_output_device=self.audio_output_device, radio_model=self.radio_model, radio_frequency=self.radio_frequency, radio_notes=self.radio_notes, audio_input_levels=self.audio_input_levels, audio_output_levels=self.audio_output_levels, cos_source=self.cos_source, cos_polarity=self.cos_polarity, cos_gpio_pin=self.cos_gpio_pin, cos_serial_device=self.cos_serial_device, cos_serial_line=self.cos_serial_line, ptt_output=self.ptt_output, ptt_gpio_pin=self.ptt_gpio_pin, ptt_serial_device=self.ptt_serial_device, ptt_serial_line=self.ptt_serial_line, ptt_polarity=self.ptt_polarity, vox_threshold_db=self.vox_threshold_db, squelch_tail_ms=self.squelch_tail_ms, tx_delay_ms=self.tx_delay_ms, dtmf_mute=self.dtmf_mute, vox_hold=self.vox_hold, vox_dtmf_hold=self.vox_dtmf_hold, tx_gain_db=self.tx_gain_db, tx_ctcss_hz=self.tx_ctcss_hz, tx_ctcss_level_db=self.tx_ctcss_level_db, record_transmissions=self.record_transmissions, recording_retention_days=self.recording_retention_days)

    @property
    def link_radio(self) -> LinkRadioConfig:
        return LinkRadioConfig(link_radio_enabled=self.link_radio_enabled, link_radio_name=self.link_radio_name, link_radio_model=self.link_radio_model, link_radio_frequency=self.link_radio_frequency, link_radio_notes=self.link_radio_notes, link_radio_input_device=self.link_radio_input_device, link_radio_output_device=self.link_radio_output_device, link_radio_input_levels=self.link_radio_input_levels, link_radio_output_levels=self.link_radio_output_levels, link_radio_cos=self.link_radio_cos, link_radio_cos_polarity=self.link_radio_cos_polarity, link_radio_cos_gpio_pin=self.link_radio_cos_gpio_pin, link_radio_cos_serial_device=self.link_radio_cos_serial_device, link_radio_cos_serial_line=self.link_radio_cos_serial_line, link_radio_vox_threshold_db=self.link_radio_vox_threshold_db, link_radio_ptt=self.link_radio_ptt, link_radio_ptt_gpio_pin=self.link_radio_ptt_gpio_pin, link_radio_ptt_serial_device=self.link_radio_ptt_serial_device, link_radio_ptt_serial_line=self.link_radio_ptt_serial_line, link_radio_ptt_polarity=self.link_radio_ptt_polarity, link_radio_tx_gain_db=self.link_radio_tx_gain_db, link_radio_tx_ctcss_hz=self.link_radio_tx_ctcss_hz, link_radio_timeout=self.link_radio_timeout, link_radio_squelch_tail_ms=self.link_radio_squelch_tail_ms, link_radio_tx_delay_ms=self.link_radio_tx_delay_ms, link_radio_courtesy_tone=self.link_radio_courtesy_tone, link_radio_mode=self.link_radio_mode)

    @property
    def remote_base(self) -> RemoteBaseConfig:
        return RemoteBaseConfig(remote_base_rigctld=self.remote_base_rigctld, remote_base_rig_mode=self.remote_base_rig_mode, remote_base_mhz=self.remote_base_mhz, remote_base_shift=self.remote_base_shift, remote_base_offset_mhz=self.remote_base_offset_mhz, remote_base_tone_hz=self.remote_base_tone_hz, remote_base_ranges=self.remote_base_ranges, remote_base_idle_minutes=self.remote_base_idle_minutes)

    @property
    def aprs(self) -> AprsConfig:
        return AprsConfig(aprs_enabled=self.aprs_enabled, aprs_server=self.aprs_server, aprs_port=self.aprs_port, aprs_callsign=self.aprs_callsign, aprs_lat=self.aprs_lat, aprs_lon=self.aprs_lon, aprs_comment=self.aprs_comment, aprs_beacon_interval=self.aprs_beacon_interval, aprs_symbol=self.aprs_symbol, aprs_frequency_mhz=self.aprs_frequency_mhz, aprs_offset_mhz=self.aprs_offset_mhz, aprs_tone_hz=self.aprs_tone_hz, aprs_map_enabled=self.aprs_map_enabled, aprs_map_radius_km=self.aprs_map_radius_km, aprs_map_hours=self.aprs_map_hours, aprs_map_tiles=self.aprs_map_tiles, distance_units=self.distance_units)

    @property
    def weather(self) -> WeatherConfig:
        return WeatherConfig(wx_alerts_enabled=self.wx_alerts_enabled, wx_lat=self.wx_lat, wx_lon=self.wx_lon, wx_min_severity=self.wx_min_severity, wx_poll_interval=self.wx_poll_interval, wx_repeat_minutes=self.wx_repeat_minutes, metar_airports=self.metar_airports)

    @property
    def monitor(self) -> MonitorConfig:
        return MonitorConfig(monitor_enabled=self.monitor_enabled, monitor_name=self.monitor_name, monitor_input_device=self.monitor_input_device, monitor_squelch=self.monitor_squelch, monitor_vox_threshold_db=self.monitor_vox_threshold_db, monitor_gpio_pin=self.monitor_gpio_pin, monitor_gpio_polarity=self.monitor_gpio_polarity, monitor_gain_db=self.monitor_gain_db, monitor_record=self.monitor_record)

    @property
    def autopatch(self) -> AutopatchConfig:
        return AutopatchConfig(autopatch_enabled=self.autopatch_enabled, autopatch_access_code=self.autopatch_access_code, autopatch_hangup_code=self.autopatch_hangup_code, autopatch_dial_string=self.autopatch_dial_string, autopatch_ten_digit_prefix=self.autopatch_ten_digit_prefix, autopatch_caller_id=self.autopatch_caller_id, autopatch_allowed=self.autopatch_allowed, autopatch_blocked=self.autopatch_blocked, autopatch_max_call_seconds=self.autopatch_max_call_seconds, autopatch_ring_seconds=self.autopatch_ring_seconds, autopatch_incoming_enabled=self.autopatch_incoming_enabled, autopatch_incoming_pin=self.autopatch_incoming_pin)

    @property
    def mailbox(self) -> MailboxConfig:
        return MailboxConfig(mailbox_enabled=self.mailbox_enabled, mailbox_leave_code=self.mailbox_leave_code, mailbox_play_code=self.mailbox_play_code, mailbox_delete_code=self.mailbox_delete_code, mailbox_max_seconds=self.mailbox_max_seconds, mailbox_retention_days=self.mailbox_retention_days, mailbox_reminder_minutes=self.mailbox_reminder_minutes)

    @property
    def net(self) -> NetConfig:
        return NetConfig(net_name=self.net_name, net_tot_duration=self.net_tot_duration, net_hang_time=self.net_hang_time, net_courtesy_tone_style=self.net_courtesy_tone_style, net_courtesy_tone_asset_id=self.net_courtesy_tone_asset_id, net_hold_autopatch=self.net_hold_autopatch, net_hold_announcements=self.net_hold_announcements, net_links=self.net_links, net_link_node=self.net_link_node, net_start_say=self.net_start_say, net_end_say=self.net_end_say, net_max_minutes=self.net_max_minutes, net_schedules=self.net_schedules)

    @property
    def gpio_fan(self) -> GpioFanConfig:
        return GpioFanConfig(gpio_pins=self.gpio_pins, gpio_schedules=self.gpio_schedules, fan_output=self.fan_output, fan_cm108_pin=self.fan_cm108_pin, fan_gpio_pin=self.fan_gpio_pin, fan_polarity=self.fan_polarity, fan_run_on_minutes=self.fan_run_on_minutes, fan_temp_c=self.fan_temp_c)

    @property
    def homeassistant(self) -> HomeassistantConfig:
        return HomeassistantConfig(homeassistant_url=self.homeassistant_url, homeassistant_say_result=self.homeassistant_say_result)

    @property
    def linking(self) -> LinkingConfig:
        return LinkingConfig(link_favorites=self.link_favorites, link_schedules=self.link_schedules, gmrs_mode=self.gmrs_mode)

    @property
    def backup(self) -> BackupConfig:
        return BackupConfig(backup_enabled=self.backup_enabled, backup_interval_hours=self.backup_interval_hours, backup_keep=self.backup_keep, backup_include_recordings=self.backup_include_recordings)

    @property
    def transcription(self) -> TranscriptionConfig:
        return TranscriptionConfig(transcription_engine=self.transcription_engine, transcription_url=self.transcription_url, transcription_model=self.transcription_model)

    @property
    def public_page(self) -> PublicPageConfig:
        return PublicPageConfig(public_page_mode=self.public_page_mode, public_page_text=self.public_page_text, public_page_audio=self.public_page_audio, public_page_max_listeners=self.public_page_max_listeners)


    def to_flat_dict(self) -> dict[str, Any]:
        """Wire / persistence shape: one flat object of field names."""
        return dataclasses.asdict(self)

    @classmethod
    def from_flat_dict(cls, data: Mapping[str, Any]) -> "RepeaterConfig":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def replace_flat(self, **overrides: Any) -> "RepeaterConfig":
        """Like dataclasses.replace, for flat field names (or a whole facet)."""
        flat = self.to_flat_dict()
        for key, value in overrides.items():
            if key in FACET_FIELD_NAMES:
                if not dataclasses.is_dataclass(value):
                    raise TypeError(f"{key} must be a facet dataclass, got {type(value)!r}")
                for fname, fval in dataclasses.asdict(value).items():
                    flat[fname] = fval
            else:
                flat[key] = value
        return type(self).from_flat_dict(flat)

    def with_facet(self, facet_name: str, facet: Any) -> "RepeaterConfig":
        return self.replace_flat(**{facet_name: facet})


CONFIG_FIELDS = {f.name for f in fields(RepeaterConfig)}


def facet_for_field(field_name: str) -> str:
    return _FIELD_TO_FACET[field_name]
