"""Airport weather from DTMF: a `metar` macro speaks one airport's report
(its argument is the ICAO code), or every airport on the Weather page's list
when the argument is blank. See wx.metar for the data and wording."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from playout.renderer import TTS_PREFIX
from wx.metar import MetarCache, speech_text, spell

from .service import RepeaterService

_logger = logging.getLogger("moreopenrepeater.airport_weather")

MAX_AIRPORTS_SPOKEN = 3


class AirportWeather:
    def __init__(self, service: RepeaterService, cache: MetarCache | None = None, wall_clock: Callable[[], float] = time.time) -> None:
        self._service = service
        self._cache = cache or MetarCache()
        self._wall_clock = wall_clock
        service.metar_hook = self._dtmf

    def _name(self, icao: str) -> str:
        airport = next((a for a in self._service.config.metar_airports if a.get("icao") == icao), None)
        return (airport or {}).get("name", "").strip() or spell(icao)

    def text(self, icao: str = "") -> str:
        """What the repeater says for `icao`, or for every listed airport. Blocks on a fetch."""
        icaos = [icao] if icao else [a["icao"] for a in self._service.config.metar_airports][:MAX_AIRPORTS_SPOKEN]
        if not icaos:
            return "No airports are set up."
        reports = self._cache.get(icaos)
        units = self._service.config.distance_units
        now = self._wall_clock()
        parts = []
        for code in icaos:
            report = reports.get(code)
            if report is None:
                parts.append(f"{self._name(code)} weather is not available.")
            else:
                parts.append(speech_text(report, self._name(code), units, now))
        return " ".join(parts)

    def _dtmf(self, icao: str) -> None:
        async def run() -> None:
            try:
                text = await asyncio.get_running_loop().run_in_executor(None, self.text, icao)
            except Exception:
                _logger.exception("airport weather for %r failed", icao)
                text = "Airport weather is not available."
            self._service.speak(TTS_PREFIX + text)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._service.speak(TTS_PREFIX + self.text(icao))
            return
        loop.create_task(run())
