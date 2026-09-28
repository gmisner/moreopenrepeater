"""National Weather Service active alerts (https://api.weather.gov).

Split into pure pieces so the interesting logic is testable offline:
  - `parse_alerts` turns the API's GeoJSON into `WeatherAlert`s;
  - `AlertTracker` decides which alerts to announce, so an alert is spoken
    once when it's issued -- not again every time the NWS re-issues it as
    an "Update" (which gets a new id but references the one before);
  - `speech_text` is what the repeater says;
  - `fetch_active_alerts` is the one blocking network call (run it in a
    thread). The NWS asks every client to send an identifying User-Agent.
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

API_URL = "https://api.weather.gov/alerts/active"
USER_AGENT = "moreopenrepeater (https://github.com/gmisner/moreopenrepeater)"
TIMEOUT_SECONDS = 15
SEVERITIES = ("Minor", "Moderate", "Severe", "Extreme")
_MAX_SPOKEN_AREA_LENGTH = 120
_TIME_ZONE = re.compile(r"\b(?:[ecmp][sd]t|ak[sd]t|hst|chst|sst|utc)\b")


@dataclass(frozen=True)
class WeatherAlert:
    id: str
    event: str
    severity: str
    urgency: str
    headline: str
    nws_headline: str
    area: str
    message_type: str
    expires: Optional[str]
    ends: Optional[str]
    references: tuple[str, ...] = ()


def parse_alerts(geojson: dict) -> list[WeatherAlert]:
    alerts = []
    for feature in geojson.get("features", []):
        p = feature.get("properties", {})
        if p.get("status") != "Actual":
            continue  # skip "Test"/"Exercise" messages
        nws_headline = (p.get("parameters") or {}).get("NWSheadline") or [""]
        alerts.append(
            WeatherAlert(
                id=p.get("id") or feature.get("id", ""),
                event=p.get("event") or "Weather alert",
                severity=p.get("severity") or "Unknown",
                urgency=p.get("urgency") or "Unknown",
                headline=p.get("headline") or "",
                nws_headline=nws_headline[0] or "",
                area=p.get("areaDesc") or "",
                message_type=p.get("messageType") or "Alert",
                expires=p.get("expires"),
                ends=p.get("ends"),
                references=tuple(r.get("identifier", "") for r in p.get("references") or []),
            )
        )
    return alerts


def meets_severity(alert: WeatherAlert, minimum: str) -> bool:
    if alert.severity not in SEVERITIES:
        return minimum == "Minor"  # "Unknown": only when the user wants everything
    return SEVERITIES.index(alert.severity) >= SEVERITIES.index(minimum)


def _sentence_case(text: str) -> str:
    # NWSheadline is ALL CAPS, which some TTS engines spell out letter by
    # letter -- but time zones should stay capitalized so they *are* spelled.
    return _TIME_ZONE.sub(lambda m: m.group(0).upper(), text.capitalize())


def speech_text(alert: WeatherAlert) -> str:
    detail = _sentence_case(alert.nws_headline) if alert.nws_headline else alert.headline
    parts = [f"National Weather Service {alert.event}."]
    area = alert.area.replace(";", ",")
    if area and len(area) <= _MAX_SPOKEN_AREA_LENGTH:
        parts.append(f"For {area}.")
    if detail:
        parts.append(detail.rstrip(".") + ".")
    return " ".join(parts)


class AlertTracker:
    """Remembers what's been announced, across polls."""

    def __init__(self) -> None:
        self._last_announced: dict[str, datetime] = {}

    def was_announced(self, alert_id: str) -> bool:
        return alert_id in self._last_announced

    def update(
        self,
        alerts: list[WeatherAlert],
        now: datetime,
        minimum_severity: str = "Severe",
        repeat_every: Optional[timedelta] = None,
    ) -> list[WeatherAlert]:
        """Record a poll's results; return the alerts to announce now.

        An update inherits its predecessor's "last announced" time, so it's
        only spoken again if `repeat_every` has elapsed."""
        wanted = [a for a in alerts if meets_severity(a, minimum_severity)]
        last_announced: dict[str, datetime] = {}
        for alert in wanted:
            times = [self._last_announced[i] for i in (alert.id, *alert.references) if i in self._last_announced]
            if times:
                last_announced[alert.id] = max(times)

        to_announce = []
        for alert in wanted:
            previous = last_announced.get(alert.id)
            if previous is None or (repeat_every and now - previous >= repeat_every):
                to_announce.append(alert)
                last_announced[alert.id] = now

        self._last_announced = last_announced
        return to_announce


def fetch_active_alerts(lat: float, lon: float, contact: str = "", timeout: float = TIMEOUT_SECONDS) -> dict:
    user_agent = f"{USER_AGENT} {contact}".strip()
    request = urllib.request.Request(
        f"{API_URL}?point={lat:.4f},{lon:.4f}",
        headers={"User-Agent": user_agent, "Accept": "application/geo+json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)
