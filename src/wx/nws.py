"""National Weather Service active alerts (https://api.weather.gov).

Split into pure pieces so the interesting logic is testable offline:
  - `parse_alerts` turns the API's GeoJSON into `WeatherAlert`s;
  - `AlertTracker` decides which alerts to announce, so an alert is spoken
    once when it's issued -- not again every time the NWS re-issues it as
    an "Update" (which gets a new id but references the one before);
  - `speech_text` is what the repeater says;
  - `alert_areas` is GeoJSON for the map: an alert's own polygon, or the
    outlines of its forecast/county zones when it doesn't have one;
  - `fetch_active_alerts` and `fetch_zone_geometry` are the blocking network
    calls (run them in a thread). The NWS asks every client to send an
    identifying User-Agent.
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

API_URL = "https://api.weather.gov/alerts/active"
ZONE_URL_PREFIX = "https://api.weather.gov/zones/"
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
    zones: tuple[str, ...] = ()  # zone URLs, for outlines when there's no polygon
    geometry: Optional[dict] = field(default=None, compare=False)


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
                zones=tuple(z for z in p.get("affectedZones") or [] if z.startswith(ZONE_URL_PREFIX)),
                geometry=feature.get("geometry") or None,
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


def alert_areas(alerts: list[WeatherAlert], zone_shapes: dict[str, dict]) -> dict:
    features = []
    for alert in alerts:
        geometry = alert.geometry
        if geometry is None:
            shapes = [zone_shapes[z] for z in alert.zones if z in zone_shapes]
            if not shapes:
                continue
            geometry = shapes[0] if len(shapes) == 1 else {"type": "GeometryCollection", "geometries": shapes}
        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "id": alert.id, "event": alert.event, "severity": alert.severity,
                "headline": alert.headline, "area": alert.area,
            },
        })
    return {"type": "FeatureCollection", "features": features}


def missing_zones(alerts: list[WeatherAlert], zone_shapes: dict[str, dict]) -> list[str]:
    wanted = dict.fromkeys(z for a in alerts if a.geometry is None for z in a.zones)
    return [z for z in wanted if z not in zone_shapes]


def _get_json(url: str, contact: str, timeout: float) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": f"{USER_AGENT} {contact}".strip(), "Accept": "application/geo+json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def fetch_zone_geometry(url: str, contact: str = "", timeout: float = TIMEOUT_SECONDS) -> Optional[dict]:
    if not url.startswith(ZONE_URL_PREFIX):
        raise ValueError(f"not an NWS zone: {url}")
    return _get_json(url, contact, timeout).get("geometry")


def fetch_active_alerts(lat: float, lon: float, contact: str = "", timeout: float = TIMEOUT_SECONDS) -> dict:
    return _get_json(f"{API_URL}?point={lat:.4f},{lon:.4f}", contact, timeout)
