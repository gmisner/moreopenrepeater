"""NWS weather alert tracking and spoken summaries."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

from playout.renderer import TTS_PREFIX
from wx.nws import AlertTracker, WeatherAlert, meets_severity, parse_alerts, speech_text

from .snapshot import WEATHER_SUMMARY_MAX

if TYPE_CHECKING:
    from .facade import RepeaterService

_logger = logging.getLogger("moreopenrepeater.service")


class WeatherSupport:
    """Owns alert state from NWS polls and builds announcement clips."""

    def __init__(self, host: "RepeaterService") -> None:
        self._host = host
        self.tracker = AlertTracker()
        self.alerts: list[WeatherAlert] = []
        self.last_checked: Optional[datetime] = None
        self.last_error: Optional[str] = None

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
