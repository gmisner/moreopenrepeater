"""Optional QRZ.com callsign lookup for net check-ins.

Credentials live in their own StateStore file (not RepeaterConfig / backups).
Lookups use QRZ's XML data service over HTTP with a short timeout, never on
the radio/PTT path. Failures return empty fields so net control can keep typing.
"""
from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, Optional

from .persistence import StateStore

_logger = logging.getLogger("moreopenrepeater.qrz")

QRZ_URL = "https://xmldata.qrz.com/xml/current/"
AGENT = "moreopenrepeater"
LOOKUP_TIMEOUT_SECONDS = 8.0

# QRZ returns a single US license letter for many stations.
_LICENSE_CLASS = {
    "E": "Extra",
    "G": "General",
    "T": "Technician",
    "A": "Advanced",
    "N": "Novice",
    "P": "Technician Plus",
}


@dataclass(frozen=True)
class QrzCredentials:
    username: str = ""
    password: str = ""


@dataclass(frozen=True)
class CallsignLookup:
    callsign: str
    found: bool
    configured: bool
    name: str = ""
    location: str = ""
    license_class: str = ""


def _text(node: Optional[ET.Element], tag: str) -> str:
    if node is None:
        return ""
    child = node.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _license_class(raw: str) -> str:
    key = raw.strip().upper()
    return _LICENSE_CLASS.get(key, raw.strip())


def _empty(callsign: str, configured: bool) -> CallsignLookup:
    return CallsignLookup(callsign=callsign.strip().upper(), found=False, configured=configured)


class QrzBook:
    """`store=None` keeps credentials in memory (tests)."""

    def __init__(
        self,
        store: Optional[StateStore] = None,
        *,
        fetch: Optional[Callable[[str], str]] = None,
        timeout: float = LOOKUP_TIMEOUT_SECONDS,
    ) -> None:
        self._store = store
        self._fetch = fetch or self._http_get
        self._timeout = timeout
        self._session_key = ""
        saved = (store.load() if store is not None else None) or {}
        self.credentials = QrzCredentials(
            username=str(saved.get("username") or "").strip(),
            password=str(saved.get("password") or ""),
        )

    @property
    def configured(self) -> bool:
        return bool(self.credentials.username and self.credentials.password)

    def status(self) -> dict:
        return {
            "username": self.credentials.username,
            "password_set": bool(self.credentials.password),
            "configured": self.configured,
        }

    def update(self, *, username: Optional[str] = None, password: Optional[str] = None) -> dict:
        current = self.credentials
        next_username = current.username if username is None else username.strip()
        if password is None:
            next_password = current.password
        else:
            next_password = password
        self.credentials = QrzCredentials(username=next_username, password=next_password)
        self._session_key = ""
        if self._store is not None:
            self._store.save({"username": self.credentials.username, "password": self.credentials.password})
        return self.status()

    def lookup(self, callsign: str) -> CallsignLookup:
        cleaned = callsign.strip().upper()
        if not cleaned:
            return _empty(cleaned, self.configured)
        if not self.configured:
            return _empty(cleaned, False)
        try:
            return self._lookup_once(cleaned) or self._lookup_once(cleaned, refresh_session=True) or _empty(cleaned, True)
        except Exception as error:  # soft-fail: network, XML, auth
            _logger.info("qrz lookup for %s failed: %s", cleaned, type(error).__name__)
            self._session_key = ""
            return _empty(cleaned, True)

    def _lookup_once(self, callsign: str, *, refresh_session: bool = False) -> Optional[CallsignLookup]:
        if refresh_session or not self._session_key:
            self._session_key = self._login()
            if not self._session_key:
                return None
        root = self._get({"s": self._session_key, "callsign": callsign})
        session = root.find("Session") if root is not None else None
        if session is not None and _text(session, "Error"):
            self._session_key = ""
            return None
        call = root.find("Callsign") if root is not None else None
        if call is None:
            return None
        first = _text(call, "fname")
        last = _text(call, "name")
        name = " ".join(part for part in (first, last) if part).strip()
        city = _text(call, "addr2")
        state = _text(call, "state")
        location = ", ".join(part for part in (city, state) if part)
        return CallsignLookup(
            callsign=_text(call, "call") or callsign,
            found=True,
            configured=True,
            name=name,
            location=location,
            license_class=_license_class(_text(call, "class")),
        )

    def _login(self) -> str:
        root = self._get(
            {
                "username": self.credentials.username,
                "password": self.credentials.password,
                "agent": AGENT,
            }
        )
        session = root.find("Session") if root is not None else None
        key = _text(session, "Key")
        if not key:
            error = _text(session, "Error") or "no session key"
            _logger.info("qrz login failed: %s", error)
        return key

    def _get(self, params: dict[str, str]) -> Optional[ET.Element]:
        query = urllib.parse.urlencode(params)
        xml_text = self._fetch(f"{QRZ_URL}?{query}")
        return ET.fromstring(xml_text)

    def _http_get(self, url: str) -> str:
        request = urllib.request.Request(url, headers={"User-Agent": AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace") if error.fp else ""
            if body:
                return body
            raise
