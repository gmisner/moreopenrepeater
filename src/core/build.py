"""Build the core's parts for a process that runs without the web app.

This mirrors the core half of `api.runtime.AppRuntime`'s constructor, and the
two must stay in step (a later change should make the app call this too; it is
not done here so the normal start-up path stays untouched). What is left out is
everything that is not the radio: accounts, the dashboard, backups, updates,
alerts, weather fetching, APRS, hub, mailbox, transcripts, streaming and
Home Assistant.

Nothing here imports a web framework.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from link.asterisk_files import asterisk_files_from_env
from playout.renderer import ClipRenderer
from playout.tts import detect_tts

from api.activity import ActivityRecorder, ActivityStore
from api.allstar_audio import AllStarAudio, usrp_listen_from_env, usrp_settings_from_env
from api.allstar_node import AllStarNode
from api.assets import AudioAssetStore
from api.audio_devices import set_cache_path
from api.autopatch import Autopatch, patch_settings_from_env
from api.boards import run_amixer
from api.core_runtime import CoreRuntime
from api.dmr import DmrBridge, dmr_dir_from_env
from api.dmr_audio import DmrAudio, dmr_usrp_settings_from_env
from api.dmr_restart import DmrRestartControl, dmr_restart_settings_from_env
from api.gpio import GpioControl
from api.health import SystemProbe
from api.link_radio import LinkRadio
from api.live_audio import LiveAudio, cm108_from_env, link_cm108_from_env
from api.log_hub import LogHub
from api.logging_config import configure_logging
from api.monitor_receiver import MonitorReceiverService
from api.persistence import StateStore
from api.recordings import RecordingStore
from api.remote_base import RemoteBase
from api.service import RepeaterService
from api.tx_fan import TxFan
from api.watchdog import Watchdog, exit_for_restart, exit_on_stall_enabled, stall_seconds_from_env

from .codes import CoreCodeChecker, paths as codes_paths
from .config_boundary import load_config

_logger = logging.getLogger("moreopenrepeater.core")

REPO_DIR = Path(__file__).resolve().parent.parent.parent


@dataclass
class CoreApp:
    runtime: CoreRuntime
    service: RepeaterService
    activity_store: ActivityStore
    state_path: Path
    #: Settings that were replaced because they were not safe numbers.
    config_notes: list[str]
    assets_store: AudioAssetStore
    log_hub: LogHub


def data_root(env: Mapping[str, str]) -> Path:
    return Path(env.get("MOREOPENREPEATER_DATA_DIR") or str(REPO_DIR / "data"))


def build_core(env: Optional[Mapping[str, str]] = None) -> CoreApp:
    """Raises `core.config_boundary.CoreConfigError` if the saved configuration
    means the core must not start."""
    env = os.environ if env is None else env
    root = data_root(env)
    state_path = root / "state.json"
    log_path = Path(env.get("MOREOPENREPEATER_LOG_PATH") or str(root / "moreopenrepeater.log"))

    # Check before anything is built, and before anything is opened or keyed.
    config, config_notes = load_config(state_path)

    log_hub = LogHub()  # the API reads it over the control socket (GET /logs)
    configure_logging(log_path, hub=log_hub, module="core")
    set_cache_path(root / "audio-device-ids.json")

    assets_store = AudioAssetStore(root / "audio")
    recordings = RecordingStore(None)
    renderer = ClipRenderer(assets_store.path_for, tts=detect_tts(), recording_path=recordings.path_for)
    service = RepeaterService(config=config, state_store=StateStore(state_path))
    # The service loads the file itself too (for macros and announcements); keep
    # the checked values it was given for the settings the check may have replaced.
    if service.saved_config != config:
        service.update_config(**{k: getattr(config, k) for k in _checked_names(config_notes)})
    service.renderer = renderer
    # The core sees every transmission, so it records them, in the file the dashboard's history has
    # always been in. The API reads them over the control socket.
    activity_store = ActivityStore(data_root(env) / "activity.db")
    service.activity = ActivityRecorder(activity_store)

    cm108 = cm108_from_env(env)
    live_audio = LiveAudio(service, renderer, cm108=cm108, recordings=recordings, run_mixer=run_amixer)
    monitor_receiver = MonitorReceiverService(service, renderer.sample_rate, RecordingStore(None))
    cm108 = live_audio.cm108
    link_radio = LinkRadio(
        service,
        renderer,
        sending=lambda: live_audio.repeating_voice,
        attach_port=live_audio.set_port,
        find_cm108=lambda: link_cm108_from_env(env, cm108),
        run_mixer=run_amixer,
    )
    service.link_radio_play = link_radio.play_clip
    remote_base = RemoteBase(service)
    gpio = GpioControl(service, cm108)
    service.programming.gpio_command = gpio.run_command
    # One-time codes are the core's own (core.codes): a needs-code macro must work with the API down.
    codes = CoreCodeChecker(*codes_paths(root))
    service.programming.code_checker = codes.check
    service.programming.codes_locked = codes.locked
    autopatch = Autopatch(service, patch_settings_from_env(env))
    allstar_node = AllStarNode(
        asterisk_files_from_env(env, backup_dir=root / "asterisk-backups"),
        autopatch.settings,
        AllStarAudio(service, None),
        listen=usrp_listen_from_env(env),
        address=env.get("MOREOPENREPEATER_USRP_ADDRESS"),
        manual=usrp_settings_from_env(env),
    )
    dmr_audio = DmrAudio(service, dmr_usrp_settings_from_env(env))
    dmr = DmrBridge(
        dmr_dir_from_env(env),
        dmr_audio,
        DmrRestartControl(dmr_restart_settings_from_env(env)),
        usrp=dmr_usrp_settings_from_env(env),
    )
    tx_fan = TxFan(service, cm108, temperature=SystemProbe(root).temperature_c)
    watchdog = Watchdog(stall_seconds=stall_seconds_from_env(env))
    if exit_on_stall_enabled(env):  # a container: nothing else restarts a core whose audio has stalled
        watchdog.restart = exit_for_restart
    runtime = CoreRuntime(
        service=service,
        renderer=renderer,
        live_audio=live_audio,
        monitor_receiver=monitor_receiver,
        link_radio=link_radio,
        remote_base=remote_base,
        gpio=gpio,
        tx_fan=tx_fan,
        autopatch=autopatch,
        allstar_node=allstar_node,
        dmr=dmr,
        dmr_audio=dmr_audio,
        watchdog=watchdog,
    )
    return CoreApp(runtime, service, activity_store, state_path, config_notes, assets_store, log_hub)


def _checked_names(notes: list[str]) -> list[str]:
    return [note.split(" ", 1)[0] for note in notes]
