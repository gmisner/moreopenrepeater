"""The core container wires the restart-on-stall watchdog from its environment (#221, #229)."""
import json

from api import core_runtime
from api.watchdog import SOUND_CARD_GRACE_SECONDS, Watchdog, exit_for_restart
from core.build import build_core


def build(tmp_path, **env):
    (tmp_path / "state.json").write_text(json.dumps({"config": {"callsign": "W1AW", "audio_enabled": False}}))
    return build_core({"MOREOPENREPEATER_DATA_DIR": str(tmp_path), "MOREOPENREPEATER_LOG_PATH": str(tmp_path / "c.log"), **env})


def test_with_the_switch_on_the_core_restarts_itself_on_a_stall(tmp_path):
    watchdog = build(tmp_path, MOREOPENREPEATER_EXIT_ON_STALL="1").runtime.watchdog
    assert watchdog.restart is exit_for_restart
    assert [name for name, _ in watchdog._watched] == [
        "controller", "audio engine", "link radio audio engine", "sound card",
    ]
    assert watchdog._limits == {"sound card": SOUND_CARD_GRACE_SECONDS}


def test_only_a_container_restarts_itself_over_a_lost_sound_card(tmp_path):
    watchdog = build(tmp_path).runtime.watchdog
    assert "sound card" not in [name for name, _ in watchdog._watched]


def test_without_it_the_core_leaves_restarting_to_systemd(tmp_path):
    assert build(tmp_path).runtime.watchdog.restart is None


def test_a_container_core_starts_checking_on_its_own_timer_when_it_is_ready(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(Watchdog, "start", lambda self, interval: started.append(interval))
    monkeypatch.setattr(core_runtime, "sd_notify", lambda *a, **k: False)
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    build(tmp_path, MOREOPENREPEATER_EXIT_ON_STALL="1").runtime.notify_ready()
    assert started == [2.0]


def test_a_core_with_no_systemd_and_no_switch_starts_no_watchdog(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(Watchdog, "start", lambda self, interval: started.append(interval))
    monkeypatch.setattr(core_runtime, "sd_notify", lambda *a, **k: False)
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    build(tmp_path).runtime.notify_ready()
    assert started == []


def test_under_systemd_the_systemd_period_is_used_not_the_container_one(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(Watchdog, "start", lambda self, interval: started.append(interval))
    monkeypatch.setattr(core_runtime, "sd_notify", lambda *a, **k: True)
    monkeypatch.setenv("WATCHDOG_USEC", "30000000")
    monkeypatch.delenv("WATCHDOG_PID", raising=False)
    build(tmp_path, MOREOPENREPEATER_EXIT_ON_STALL="1").runtime.notify_ready()
    assert started == [15.0]


def test_the_stall_limit_from_the_environment_reaches_the_watchdog(tmp_path):
    assert build(tmp_path).runtime.watchdog.stall_seconds == 10.0
    assert build(tmp_path, MOREOPENREPEATER_STALL_SECONDS="25").runtime.watchdog.stall_seconds == 25.0


def test_a_short_limit_makes_the_container_check_more_often(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(Watchdog, "start", lambda self, interval: started.append(interval))
    monkeypatch.setattr(core_runtime, "sd_notify", lambda *a, **k: False)
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    build(tmp_path, MOREOPENREPEATER_EXIT_ON_STALL="1", MOREOPENREPEATER_STALL_SECONDS="3").runtime.notify_ready()
    assert started == [0.75]
