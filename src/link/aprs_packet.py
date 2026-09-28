"""Decodes positions out of APRS packets (TNC2 text, as APRS-IS sends them).

Covers what stations near a repeater actually send, per APRS101.PDF:
  - uncompressed positions ('!' '=' '/' '@'), with the course/speed and
    altitude extensions and complete weather reports ('_' symbol);
  - compressed positions (base-91, including compressed course/speed);
  - Mic-E ('`' and "'"), which most Kenwood/Yaesu mobiles and HTs send --
    the latitude and a few flags ride in the *destination* callsign;
  - objects (';') and items (')'), which is how most repeaters, nets and
    events are put on the map, including "killed" ones being removed;
  - third-party packets ('}'), unwrapped.

Anything else (messages, telemetry, status, positionless weather) returns
None: this module answers "where is it and what is it", nothing more.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

KNOTS_TO_KMH = 1.852
FEET_TO_M = 0.3048

# Symbol codes (primary table) for things that move.
_MOBILE_CODES = set("><'^OPRUXYabfjkstuv[=")


@dataclass(frozen=True)
class AprsPosition:
    name: str  # callsign, or the object/item name
    source: str  # the station that sent it
    lat: float
    lon: float
    symbol_table: str
    symbol_code: str
    comment: str = ""
    course: Optional[int] = None  # degrees true
    speed_kmh: Optional[float] = None
    altitude_m: Optional[float] = None
    weather: Optional[dict] = None
    kind: str = "station"  # "station" | "object" | "item"
    killed: bool = False  # objects/items: remove from the map


def category(p: AprsPosition) -> str:
    """Coarse grouping for map filters and the spoken summary."""
    code, table = p.symbol_code, p.symbol_table
    if code == "_" or p.weather:
        return "weather"
    if code == "r" or (p.kind != "station" and re.match(r"^\d{3}\.\d", p.name)):
        return "repeater"
    if code == "#" or (code == "&" and table != "/"):
        return "digipeater"
    if (p.speed_kmh or 0) > 1 or (table == "/" and code in _MOBILE_CODES):
        return "mobile"
    return "fixed"


# -- helpers ----------------------------------------------------------------


def _base91(chars: str) -> int:
    value = 0
    for c in chars:
        value = value * 91 + ord(c) - 33
    return value


_UNCOMPRESSED = re.compile(
    r"^(\d{2})([\d ]{2}\.[\d ]{2})([NS])([/\\0-9A-Za-j])(\d{3})([\d ]{2}\.[\d ]{2})([EW])(.)"
)
# Width, in minutes, of the area hidden by 0-4 blanked digits.
_AMBIGUITY_MINUTES = (0.0, 0.1, 1.0, 10.0, 60.0)
_COURSE_SPEED = re.compile(r"^(\d{3})/(\d{3})")
_ALTITUDE = re.compile(r"/A=(-?\d{5,6})")
_WEATHER_FIELDS = {
    "c": ("wind_dir", 3),
    "s": ("wind_mph", 3),
    "g": ("wind_gust_mph", 3),
    "t": ("temp_f", 3),
    "r": ("rain_1h_in", 3),
    "p": ("rain_24h_in", 3),
    "P": ("rain_midnight_in", 3),
    "h": ("humidity", 2),
    "b": ("pressure_mbar", 5),
    "L": ("luminosity", 3),
    "l": ("luminosity_high", 3),
}


_COMMENT_TELEMETRY = re.compile(r"\|[!-{]{4,14}\|")  # base-91 "|ss1122|"
_DAO = re.compile(r"![Ww][!-{]{2}!")  # extra position precision
_SOFTWARE_TAG = re.compile(r"\{[A-Za-z0-9@]{2,6}\}$")  # e.g. UI-View's "{UIV32N}"


def _clean_comment(comment: str) -> str:
    comment = _DAO.sub("", _COMMENT_TELEMETRY.sub("", comment)).strip()
    return _SOFTWARE_TAG.sub("", comment).strip()


def _uncompressed(body: str) -> Optional[tuple[float, float, str, str, str]]:
    m = _UNCOMPRESSED.match(body)
    if not m:
        return None
    lat_deg, lat_min, ns, table, lon_deg, lon_min, ew, code = m.groups()
    # Blanked digits hide the exact position; plot the middle of the area.
    # Longitude has the same ambiguity as latitude, whatever it says.
    half = _AMBIGUITY_MINUTES[min(lat_min.count(" "), 4)] / 2
    lat = int(lat_deg) + (float(lat_min.replace(" ", "0")) + half) / 60
    lon = int(lon_deg) + (float(lon_min.replace(" ", "0")) + half) / 60
    if lat > 90 or lon > 180:
        return None
    return (-lat if ns == "S" else lat, -lon if ew == "W" else lon, table, code, body[19:])


def _compressed(body: str) -> Optional[tuple[float, float, str, str, str, Optional[int], Optional[float], Optional[float]]]:
    if len(body) < 13:
        return None
    table = body[0]
    if not (table in "/\\" or "A" <= table <= "Z" or "a" <= table <= "j"):
        return None
    y, x, code, cs, t = body[1:5], body[5:9], body[9], body[10:12], body[12]
    if not all("!" <= c <= "{" for c in y + x):
        return None
    lat = 90 - _base91(y) / 380926
    lon = -180 + _base91(x) / 190463
    if "a" <= table <= "j":
        table = str(ord(table) - ord("a"))  # overlay digit
    course = speed = altitude = None
    if cs[0] != " ":
        c, s = ord(cs[0]) - 33, ord(cs[1]) - 33
        if (ord(t) - 33) & 0x18 == 0x10:
            altitude = 1.002 ** (c * 91 + s) * FEET_TO_M
        elif 0 <= c <= 89:
            course = c * 4 or 360
            speed = (1.08**s - 1) * KNOTS_TO_KMH
    return lat, lon, table, code, body[13:], course, speed, altitude


def _weather(comment: str) -> tuple[dict, str]:
    weather: dict = {}
    m = re.match(r"^([\d. ]{3})/([\d. ]{3})", comment)
    if m:
        if m.group(1).strip().isdigit():
            weather["wind_dir"] = int(m.group(1))
        if m.group(2).strip().isdigit():
            weather["wind_mph"] = int(m.group(2))
        comment = comment[7:]
    while comment:
        field = _WEATHER_FIELDS.get(comment[0])
        if field is None:
            break
        name, width = field
        raw = comment[1 : 1 + width]
        if not re.fullmatch(r"-?\d+|\.+| +", raw) or len(raw) != width:
            break
        if raw.lstrip("-").isdigit():
            value: float = int(raw)
            if name.startswith("rain"):
                value /= 100
            elif name == "pressure_mbar":
                value /= 10
            elif name == "humidity" and value == 0:
                value = 100
            weather[name] = value
        comment = comment[1 + width :]
    return weather, comment


def _altitude(comment: str) -> tuple[Optional[float], str]:
    m = _ALTITUDE.search(comment)
    if not m:
        return None, comment
    return int(m.group(1)) * FEET_TO_M, comment[: m.start()] + comment[m.end() :]


def _extensions(comment: str) -> tuple[Optional[int], Optional[float], Optional[float], str]:
    course = speed = None
    m = _COURSE_SPEED.match(comment)
    if m:
        c, s = int(m.group(1)), int(m.group(2))
        if 1 <= c <= 360:
            course = c
        speed = s * KNOTS_TO_KMH
        comment = comment[7:]
    altitude, comment = _altitude(comment)
    return course, speed, altitude, comment


def _position(name: str, source: str, body: str, kind: str = "station", killed: bool = False) -> Optional[AprsPosition]:
    decoded = _uncompressed(body)
    if decoded is not None:
        lat, lon, table, code, comment = decoded
        weather = None
        if code == "_":
            weather, comment = _weather(comment)
            course = speed = None
            altitude, comment = _altitude(comment)
        else:
            course, speed, altitude, comment = _extensions(comment)
    else:
        compressed = _compressed(body)
        if compressed is None:
            return None
        lat, lon, table, code, comment, course, speed, altitude = compressed
        weather = None
        if code == "_":
            weather, comment = _weather(comment)
        if altitude is None:
            altitude, comment = _altitude(comment)
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:
        return None  # "0,0": no GPS fix
    return AprsPosition(
        name=name,
        source=source,
        lat=round(lat, 6),
        lon=round(lon, 6),
        symbol_table=table,
        symbol_code=code,
        comment=_clean_comment(comment),
        course=course,
        speed_kmh=None if speed is None else round(speed, 1),
        altitude_m=None if altitude is None else round(altitude, 1),
        weather=weather or None,
        kind=kind,
        killed=killed,
    )


# -- Mic-E --------------------------------------------------------------------


def _mic_e_digit(c: str) -> Optional[str]:
    if "0" <= c <= "9":
        return c
    if "A" <= c <= "J":
        return str(ord(c) - ord("A"))
    if "P" <= c <= "Y":
        return str(ord(c) - ord("P"))
    if c in "KLZ":
        return "0"  # ambiguity
    return None


def _mic_e(source: str, destination: str, info: str) -> Optional[AprsPosition]:
    dest = destination.split("-")[0]
    if len(dest) != 6 or len(info) < 9:
        return None
    digits = [_mic_e_digit(c) for c in dest]
    if None in digits:
        return None
    north = "P" <= dest[3] <= "Z"
    lon_offset = 100 if "P" <= dest[4] <= "Z" else 0
    west = "P" <= dest[5] <= "Z"
    ambiguous = 0
    for c in reversed(dest[:6]):
        if c not in "KLZ":
            break
        ambiguous += 1
    unit = _AMBIGUITY_MINUTES[min(ambiguous, 4)]
    lat_minutes = float(f"{''.join(digits[2:4])}.{''.join(digits[4:6])}") + unit / 2
    lat = int("".join(digits[:2])) + lat_minutes / 60

    d = ord(info[1]) - 28 + lon_offset
    if 180 <= d <= 189:
        d -= 80
    elif 190 <= d <= 199:
        d -= 190
    m = ord(info[2]) - 28
    if m >= 60:
        m -= 60
    h = ord(info[3]) - 28
    if not (0 <= h < 100):
        return None
    lon_minutes = m + h / 100
    if unit:
        lon_minutes = math.floor(round(lon_minutes / unit, 6)) * unit + unit / 2
    lon = d + lon_minutes / 60
    if not (0 <= lat <= 90 and 0 <= lon <= 180):
        return None

    sp, dc, se = ord(info[4]) - 28, ord(info[5]) - 28, ord(info[6]) - 28
    speed = sp * 10 + dc // 10
    if speed >= 800:
        speed -= 800
    course = (dc % 10) * 100 + se
    if course >= 400:
        course -= 400

    code, table = info[7], info[8]
    comment = info[9:]
    altitude = None
    alt = re.search(r"(.{3})\}", comment)
    if alt and all("!" <= c <= "{" for c in alt.group(1)):
        altitude = _base91(alt.group(1)) - 10000
        comment = comment[: alt.start()] + comment[alt.end() :]
    # Radio model markers (APRS "Mic-E type codes"): Kenwood ">...=" / "]...=",
    # Yaesu "`..._x", Byonics "'...|3" etc. -- not part of the user's comment.
    comment = re.sub(r"^[>\]`']", "", comment)
    comment = re.sub(r"(_[\s!-~]|[=^&]|\|3)$", "", comment)

    return AprsPosition(
        name=source,
        source=source,
        lat=round(lat if north else -lat, 6),
        lon=round(-lon if west else lon, 6),
        symbol_table=table,
        symbol_code=code,
        comment=_clean_comment(comment),
        course=course if 1 <= course <= 360 else None,
        speed_kmh=round(speed * KNOTS_TO_KMH, 1),
        altitude_m=None if altitude is None else float(altitude),
    )


# -- packets ------------------------------------------------------------------

_HEADER = re.compile(r"^([A-Za-z0-9-]{1,9})>([A-Za-z0-9-]{1,9})((?:,[^:,]+)*):(.*)$", re.S)


def parse_packet(line: str) -> Optional[AprsPosition]:
    line = line.rstrip("\r\n")
    if not line or line.startswith("#"):
        return None
    m = _HEADER.match(line)
    if not m:
        return None
    source, destination, _path, info = m.groups()
    if not info:
        return None
    dti, rest = info[0], info[1:]
    try:
        if dti == "}":
            return parse_packet(rest)
        if dti in "!=":
            return _position(source, source, rest)
        if dti in "/@":
            return _position(source, source, rest[7:])
        if dti in "`'":
            return _mic_e(source, destination, info)
        if dti == ";" and len(rest) >= 17:
            name, state = rest[:9].strip(), rest[9]
            return _position(name, source, rest[17:], kind="object", killed=state == "_")
        if dti == ")":
            ends = [i for i in (rest.find("!"), rest.find("_")) if 3 <= i <= 9]
            if ends:
                end = min(ends)
                return _position(rest[:end].strip(), source, rest[end + 1 :], kind="item", killed=rest[end] == "_")
    except (ValueError, IndexError):
        return None
    return None
