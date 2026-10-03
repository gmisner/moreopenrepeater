"""Airport weather (METAR) from aviationweather.gov, spoken in plain words.

  - `speech_text` turns one decoded observation (the API's JSON, not the raw
    METAR) into what the repeater says;
  - `fetch_metars` is the blocking network call (run it in a thread);
  - `MetarCache` keeps each airport's report for `CACHE_SECONDS`, and falls
    back to an older one (its age is spoken) when the network is down.

Wind is always in knots and cloud heights in feet, as in aviation. Visibility,
temperature and pressure follow the station's distance units: miles,
Fahrenheit and inches of mercury, or kilometres, Celsius and hectopascals.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Callable, Optional
from urllib.parse import urlencode

API_URL = "https://aviationweather.gov/api/data/metar"
USER_AGENT = "moreopenrepeater (https://github.com/gmisner/moreopenrepeater)"
TIMEOUT_SECONDS = 10
CACHE_SECONDS = 600
STALE_SECONDS = 3 * 3600  # older reports aren't worth reading out
KM_PER_MILE = 1.609344
INHG_PER_HPA = 0.0295300

_DIGIT_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_COVER = {"FEW": "few", "SCT": "scattered", "BKN": "broken", "OVC": "overcast"}
_CLEAR = {"CLR", "SKC", "NSC", "NCD", "CAVOK"}
_INTENSITY = {"-": "light", "+": "heavy"}
_DESCRIPTORS = {
    "MI": "shallow", "PR": "partial", "BC": "patches of", "DR": "low drifting", "BL": "blowing",
    "SH": "showers of", "TS": "thunderstorm with", "FZ": "freezing",
}
_PHENOMENA = {
    "DZ": "drizzle", "RA": "rain", "SN": "snow", "SG": "snow grains", "IC": "ice crystals", "PL": "ice pellets",
    "GR": "hail", "GS": "small hail", "UP": "unknown precipitation", "BR": "mist", "FG": "fog", "FU": "smoke",
    "VA": "volcanic ash", "DU": "dust", "SA": "sand", "HZ": "haze", "PY": "spray", "PO": "dust whirls",
    "SQ": "squalls", "FC": "funnel cloud", "SS": "sandstorm", "DS": "duststorm",
}
_FRACTIONS = {0.125: "one eighth", 0.25: "one quarter", 0.5: "one half", 0.75: "three quarters"}


def spell(text: str) -> str:
    """'KAMA' as 'K A M A', for an airport without a spoken name."""
    return " ".join(text.upper())


def _digits(number: int, width: int = 3) -> str:
    return " ".join(_DIGIT_WORDS[int(d)] for d in f"{number:0{width}d}")


def _wind(obs: dict) -> str:
    direction, speed, gust = obs.get("wdir"), obs.get("wspd"), obs.get("wgst")
    if speed is None:
        return ""
    if speed == 0:
        return "Wind calm."
    if direction == "VRB" or not isinstance(direction, (int, float)):
        text = f"Wind variable at {speed} knots"
    else:
        text = f"Wind {_digits(int(direction))} at {speed} knots"
    if gust:
        text += f", gusting {gust}"
    return text + "."


def _miles_text(miles: float) -> str:
    whole, part = int(miles), round(miles - int(miles), 3)
    if part in _FRACTIONS:
        fraction = _FRACTIONS[part]
        if whole == 0:
            return f"{fraction} mile"
        return f"{whole} and {fraction.replace('one ', 'a ')} miles"
    rounded = round(miles)
    return f"{rounded} mile" if rounded == 1 else f"{rounded} miles"


def _visibility(obs: dict, units: str) -> str:
    raw = obs.get("visib")
    if raw is None:
        return ""
    text = str(raw)
    plus = text.endswith("+")
    try:
        miles = float(text.rstrip("+"))
    except ValueError:
        return ""
    if units == "km":
        km = miles * KM_PER_MILE
        amount = f"{round(km)} kilometers" if km >= 5 else f"{round(km, 1):g} kilometers"
        if amount == "1 kilometers":
            amount = "1 kilometer"
    else:
        amount = _miles_text(miles)
    return f"Visibility {amount}{' or more' if plus else ''}."


def _weather_token(token: str) -> str:
    words = []
    if token[:1] in _INTENSITY:
        words.append(_INTENSITY[token[0]])
        token = token[1:]
    nearby = token.startswith("VC")
    if nearby:
        token = token[2:]
    codes = [token[i:i + 2] for i in range(0, len(token), 2)]
    if codes and codes[0] in _DESCRIPTORS:
        descriptor = _DESCRIPTORS[codes.pop(0)]
        if not codes:
            descriptor = {"thunderstorm with": "thunderstorm", "showers of": "showers"}.get(descriptor, descriptor)
        words.append(descriptor)
    for code in codes:
        if code not in _PHENOMENA:
            return ""
        words.append(_PHENOMENA[code])
    if nearby:
        words.append("nearby")
    return " ".join(words)


def _weather(obs: dict) -> str:
    phrases = [p for p in (_weather_token(t) for t in (obs.get("wxString") or "").split()) if p]
    if not phrases:
        return ""
    text = ", ".join(phrases)
    return text[0].upper() + text[1:] + "."


def _sky(obs: dict) -> str:
    layers = obs.get("clouds") or []
    covers = [layer.get("cover") for layer in layers]
    if not layers:
        return "Sky clear." if obs.get("cover") in _CLEAR else ""
    if any(c in _CLEAR for c in covers):
        return "Sky clear."
    if "OVX" in covers:
        base = next((layer.get("base") for layer in layers if layer.get("cover") == "OVX"), None)
        if base is None and obs.get("vertVis") is not None:
            base = obs["vertVis"] * 100
        return f"Sky obscured, vertical visibility {base} feet." if base is not None else "Sky obscured."
    parts = []
    for layer in layers:
        words = _COVER.get(layer.get("cover"))
        if words is None:
            continue
        base = layer.get("base")
        parts.append(f"{words} at {base} feet" if base is not None else words)
    if not parts:
        return ""
    return "Clouds " + ", ".join(parts) + "."


def _degrees(celsius: float, units: str) -> str:
    value = round(celsius) if units == "km" else round(celsius * 9 / 5 + 32)
    return f"minus {-value}" if value < 0 else str(value)


def _temperature(obs: dict, units: str) -> str:
    temp, dew = obs.get("temp"), obs.get("dewp")
    if temp is None:
        return ""
    scale = "Celsius" if units == "km" else "Fahrenheit"
    if dew is None:
        return f"Temperature {_degrees(temp, units)} degrees {scale}."
    return f"Temperature {_degrees(temp, units)}, dew point {_degrees(dew, units)} degrees {scale}."


def _pressure(obs: dict, units: str) -> str:
    hpa = obs.get("altim")
    if hpa is None:
        return ""
    if units == "km":
        return f"Altimeter {round(hpa)} hectopascals."
    return f"Altimeter {hpa * INHG_PER_HPA:.2f}."


def _age(obs: dict, now: float) -> str:
    observed = obs.get("obsTime")
    if not isinstance(observed, (int, float)):
        return ""
    minutes = max(0, round((now - observed) / 60))
    if minutes < 2:
        return "just now"
    if minutes < 120:
        return f"{minutes} minutes ago"
    return f"{minutes // 60} hours ago"


def speech_text(obs: dict, name: str, units: str, now: float) -> str:
    """`now` is Unix time, to say how old the report is."""
    age = _age(obs, now)
    intro = f"{name} weather, observed {age}." if age else f"{name} weather."
    parts = [
        intro,
        _wind(obs),
        _visibility(obs, units),
        _weather(obs),
        _sky(obs),
        _temperature(obs, units),
        _pressure(obs, units),
    ]
    return " ".join(p for p in parts if p)


def fetch_metars(icaos: list[str], timeout: float = TIMEOUT_SECONDS) -> list[dict]:
    """The latest decoded report for each airport that has one. Blocks."""
    url = f"{API_URL}?{urlencode({'ids': ','.join(icaos), 'format': 'json'})}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    if not body.strip():
        return []  # the API answers 204 with no body when there are no reports
    data = json.loads(body)
    return [obs for obs in data if isinstance(obs, dict) and obs.get("icaoId")] if isinstance(data, list) else []


class MetarCache:
    def __init__(
        self,
        fetch: Callable[[list[str]], list[dict]] = fetch_metars,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._fetch = fetch
        self._clock = clock
        self._entries: dict[str, tuple[float, Optional[dict]]] = {}  # icao -> (fetched at, report or None)
        self._lock = threading.Lock()

    def get(self, icaos: list[str]) -> dict[str, Optional[dict]]:
        """Each airport's report, or None when there's none to read (unknown
        airport, or offline with nothing recent cached). Blocks on a fetch."""
        now = self._clock()
        with self._lock:
            missing = [i for i in icaos if i not in self._entries or now - self._entries[i][0] >= CACHE_SECONDS]
        if missing:
            try:
                fetched = {obs["icaoId"].upper(): obs for obs in self._fetch(missing)}
            except (OSError, ValueError):
                fetched = None
            with self._lock:
                for icao in missing:
                    if fetched is not None:
                        self._entries[icao] = (now, fetched.get(icao))
        result = {}
        with self._lock:
            for icao in icaos:
                obs = self._entries.get(icao, (0.0, None))[1]
                observed = obs.get("obsTime") if obs else None
                fresh = isinstance(observed, (int, float)) and now - observed < STALE_SECONDS
                result[icao] = obs if fresh else None
        return result
