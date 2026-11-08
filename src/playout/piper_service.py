"""Keep Piper's ONNX voice loaded in a localhost `piper.http_server` process.

Each `piper` CLI invoke cold-loads the voice (~60–110 MB). On a Pi that often
exceeds the speak timeout and falls back to espeak. A long-lived HTTP server
pays that cost once at startup; speaks then hit `/synthesize`.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen as _urlopen

_logger = logging.getLogger("moreopenrepeater.tts")

DEFAULT_PIPER_SERVICE_HOST = "127.0.0.1"
DEFAULT_PIPER_SERVICE_PORT = 8766
DEFAULT_PIPER_SERVICE_URL = f"http://{DEFAULT_PIPER_SERVICE_HOST}:{DEFAULT_PIPER_SERVICE_PORT}"
# High voices can take a couple of minutes to load into RAM on a cold Pi.
DEFAULT_READY_TIMEOUT_SECONDS = 240.0


class PiperServiceError(RuntimeError):
    pass


class PiperService:
    """Owns one `python -m piper.http_server` child. Not thread-safe for concurrent start/stop."""

    def __init__(
        self,
        *,
        python: str = sys.executable,
        voice: Optional[str] = None,
        data_dir: Optional[str] = None,
        url: str = DEFAULT_PIPER_SERVICE_URL,
        popen: Callable[..., Any] = subprocess.Popen,
        urlopen: Callable[..., Any] = _urlopen,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        env: Optional[dict[str, str]] = None,
    ) -> None:
        self.python = python
        self.voice = (voice if voice is not None else os.environ.get("PIPER_VOICE", "")).strip() or "en_GB-cori-high"
        self.data_dir = data_dir if data_dir is not None else os.environ.get("PIPER_DATA_DIR", "").strip()
        self.url = url.rstrip("/")
        self._popen = popen
        self._urlopen = urlopen
        self._sleep = sleep
        self._clock = clock
        self._env = env
        self.process: Any = None

    @property
    def info_url(self) -> str:
        return f"{self.url}/info"

    @property
    def synthesize_url(self) -> str:
        return f"{self.url}/synthesize"

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        if not self.data_dir:
            raise PiperServiceError("PIPER_DATA_DIR is not set")
        host, port = _host_port(self.url)
        argv = [
            self.python,
            "-m",
            "piper.http_server",
            "--host",
            host,
            "--port",
            str(port),
            "-m",
            self.voice,
            "--data-dir",
            self.data_dir,
        ]
        env = dict(self._env if self._env is not None else os.environ)
        _logger.info("starting Piper service (%s) at %s with voice %s", self.python, self.url, self.voice)
        self.process = self._popen(
            argv,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def wait_ready(self, timeout: float = DEFAULT_READY_TIMEOUT_SECONDS) -> bool:
        """Block until `/info` answers, or raise."""
        if self.process is None:
            raise PiperServiceError("Piper service was not started")
        deadline = self._clock() + timeout
        last_error = "not ready yet"
        while self._clock() < deadline:
            code = self.process.poll()
            if code is not None:
                raise PiperServiceError(f"Piper service exited before it was ready (code {code})")
            try:
                if self._info_ready():
                    _logger.info("Piper service is ready at %s", self.url)
                    return True
            except (URLError, HTTPError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as error:
                last_error = str(error) or type(error).__name__
            self._sleep(0.5)
        raise PiperServiceError(f"Piper service was not ready within {timeout:g}s ({last_error})")

    def start_and_wait(self, timeout: float = DEFAULT_READY_TIMEOUT_SECONDS) -> None:
        self.start()
        self.wait_ready(timeout=timeout)

    def stop(self) -> None:
        proc = self.process
        self.process = None
        if proc is None:
            return
        if proc.poll() is not None:
            return
        _logger.info("stopping Piper service")
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
                return
            except (subprocess.TimeoutExpired, TimeoutError):
                pass
            proc.kill()
            try:
                proc.wait(timeout=2)
            except (subprocess.TimeoutExpired, TimeoutError):
                pass
        except OSError:
            _logger.exception("couldn't stop Piper service")

    def _info_ready(self) -> bool:
        request = Request(self.info_url, method="GET")
        with self._urlopen(request, timeout=2) as response:
            raw = response.read()
        if not raw:
            return True
        payload = json.loads(raw.decode("utf-8"))
        return isinstance(payload, dict)


def _host_port(url: str) -> tuple[str, int]:
    # Expect http://127.0.0.1:8766 — keep parsing minimal and local-only.
    without_scheme = url.split("://", 1)[-1]
    host, _, port_s = without_scheme.partition(":")
    host = host.strip() or DEFAULT_PIPER_SERVICE_HOST
    port_s = port_s.split("/", 1)[0].strip()
    return host, int(port_s or DEFAULT_PIPER_SERVICE_PORT)
