"""NWS weather alert tracking, spoken summaries, and scheduled weather clips."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Optional

from playout.renderer import TTS_PREFIX
from wx.nws import AlertTracker, WeatherAlert, meets_severity, parse_alerts, speech_text

from .snapshot import WEATHER_SUMMARY_MAX

if TYPE_CHECKING:
    from .facade import RepeaterService

_logger = logging.getLogger("moreopenrepeater.service")

# Spoken when METAR airports aren't configured but schedule content asks for them.
_NO_AIRPORTS = "No airports are set up."


class WeatherSupport:
    """Owns alert state from NWS polls and builds announcement clips."""

    def __init__(self, host: "RepeaterService") -> None:
        self._host = host
        self.tracker = AlertTracker()
        self.alerts: list[WeatherAlert] = []
        self.last_checked: Optional[datetime] = None
        self.last_error: Optional[str] = None
        # Blocks on network; wired to AirportWeather.text("") from the runtime.
        self.metar_text: Optional[Callable[[], str]] = None
        self._next_schedule: Optional[datetime] = None
        self._schedule_fingerprint: Optional[tuple] = None

    def summary_text(self) -> str:
        wx = self._host.controller.config.weather
        if wx.wx_lat is None or wx.wx_lon is None:
            return "Weather alerts are not set up."
        if not self.alerts:
            return "There are no active weather alerts."
        count = len(self.alerts)
        intro = "There is one active weather alert." if count == 1 else f"There are {count} active weather alerts."
        return " ".join([intro] + [speech_text(a) for a in self.alerts[:WEATHER_SUMMARY_MAX]])

    def polled(self, geojson: dict, announce: bool = True) -> list[str]:
        """Record a successful NWS poll; return the clips to announce.
        With `announce=False` (a manual check while alerts are switched
        off) it only refreshes what the dashboard shows."""
        wx = self._host.controller.config.weather
        alerts = parse_alerts(geojson)
        self.alerts = [a for a in alerts if meets_severity(a, wx.wx_min_severity)]
        self.last_checked = self._host.wall_now()
        self.last_error = None
        if not announce:
            return []
        repeat = timedelta(minutes=wx.wx_repeat_minutes) if wx.wx_repeat_minutes > 0 else None
        to_announce = self.tracker.update(alerts, self._host.wall_now(), wx.wx_min_severity, repeat)
        for alert in to_announce:
            _logger.info("announcing weather alert: %s (%s)", alert.event, alert.id)
        return [self.alert_clip(a) for a in to_announce]

    def poll_failed(self, error: str) -> None:
        self.last_checked = self._host.wall_now()
        self.last_error = error

    def alert_clip(self, alert: WeatherAlert) -> str:
        return TTS_PREFIX + speech_text(alert)

    def _schedule_key(self) -> tuple:
        wx = self._host.controller.config.weather
        return (
            wx.wx_schedule_enabled,
            wx.wx_schedule_minutes,
            wx.wx_schedule_content,
            wx.wx_schedule_on_linked_radio,
        )

    def sync_schedule(self) -> None:
        """Keep the next-run clock in step with config (reset when settings change)."""
        key = self._schedule_key()
        if key == self._schedule_fingerprint:
            return
        self._schedule_fingerprint = key
        wx = self._host.controller.config.weather
        if wx.wx_schedule_enabled and wx.wx_schedule_minutes > 0:
            self._next_schedule = self._host.wall_now() + timedelta(minutes=wx.wx_schedule_minutes)
        else:
            self._next_schedule = None

    def take_due_schedule(self) -> bool:
        """If a routine weather announcement is due, advance the timer and return True."""
        self.sync_schedule()
        now = self._host.wall_now()
        if self._next_schedule is None or now < self._next_schedule:
            return False
        wx = self._host.controller.config.weather
        # Missed runs collapse to one, same as AnnouncementScheduler.due().
        self._next_schedule = now + timedelta(minutes=wx.wx_schedule_minutes)
        return True

    def scheduled_text(self) -> Optional[str]:
        """What to say for a routine weather announcement, or None to skip.

        Skips empty chatter: no airports for METAR-only, no active alerts for
        alerts-only. Blocks when METAR content needs a network fetch.
        """
        wx = self._host.controller.config.weather
        content = wx.wx_schedule_content
        parts: list[str] = []
        if content in ("metar", "both"):
            if self.metar_text is None:
                metar = _NO_AIRPORTS
            else:
                metar = self.metar_text()
            if metar and metar != _NO_AIRPORTS:
                parts.append(metar)
            elif content == "metar":
                return None
        if content in ("alerts", "both"):
            if not self.alerts:
                if content == "alerts":
                    return None
            else:
                parts.append(self.summary_text())
        return " ".join(parts) if parts else None

    def next_schedule_run(self) -> Optional[datetime]:
        self.sync_schedule()
        return self._next_schedule
