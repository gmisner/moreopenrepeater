"""Keep Kokoro's ONNX model loaded in a localhost `kokoro serve` process.

Each `kokoro speak --service off` cold-loads ~90 MB of weights. On a Pi that
often exceeds the speak timeout after reboot. A long-lived serve process pays
that cost once; speak then hits the local HTTP API.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen as _urlopen

_logger = logging.getLogger("moreopenrepeater.tts")

DEFAULT_KOKORO_SERVICE_HOST = "127.0.0.1"
DEFAULT_KOKORO_SERVICE_PORT = 8765
DEFAULT_KOKORO_SERVICE_URL = f"http://{DEFAULT_KOKORO_SERVICE_HOST}:{DEFAULT_KOKORO_SERVICE_PORT}"
# First boot after install can still be loading int8 weights into RAM.
DEFAULT_READY_TIMEOUT_SECONDS = 240.0


class KokoroServiceError(RuntimeError):
    pass


class KokoroService:
    """Owns one `kokoro serve` child. Not thread-safe for concurrent start/stop."""

    def __init__(
        self,
        binary: str,
        *,
        home: Optional[str] = None,
        url: str = DEFAULT_KOKORO_SERVICE_URL,
        popen: Callable[..., Any] = subprocess.Popen,
        urlopen: Callable[..., Any] = _urlopen,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        env: Optional[dict[str, str]] = None,
    ) -> None:
        self.binary = binary
        self.home = home if home is not None else os.environ.get("KOKORO_HOME")
        self.url = url.rstrip("/")
        self._popen = popen
        self._urlopen = urlopen
        self._sleep = sleep
        self._clock = clock
        self._env = env
        self.process: Any = None

    @property
    def health_url(self) -> str:
        return f"{self.url}/health"

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        env = dict(self._env if self._env is not None else os.environ)
        if self.home:
            env["KOKORO_HOME"] = self.home
        _logger.info("starting Kokoro service (%s serve) at %s", self.binary, self.url)
        self.process = self._popen(
            [self.binary, "serve"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def wait_ready(self, timeout: float = DEFAULT_READY_TIMEOUT_SECONDS) -> bool:
        """Block until `/health` reports ready, or raise."""
        if self.process is None:
            raise KokoroServiceError("Kokoro service was not started")
        deadline = self._clock() + timeout
        last_error = "not ready yet"
        while self._clock() < deadline:
            code = self.process.poll()
            if code is not None:
                raise KokoroServiceError(f"Kokoro service exited before it was ready (code {code})")
            try:
                if self._health_ready():
                    _logger.info("Kokoro service is ready at %s", self.url)
                    return True
            except (URLError, HTTPError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as error:
                last_error = str(error) or type(error).__name__
            self._sleep(0.5)
        raise KokoroServiceError(f"Kokoro service was not ready within {timeout:g}s ({last_error})")

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
        _logger.info("stopping Kokoro service")
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
            _logger.exception("couldn't stop Kokoro service")

    def _health_ready(self) -> bool:
        request = Request(self.health_url, method="GET")
        with self._urlopen(request, timeout=2) as response:
            raw = response.read()
        if not raw:
            return True  # HTTP 200 with empty body: treat as up
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            return True
        if "ready" in payload:
            return bool(payload["ready"])
        if "status" in payload:
            return str(payload["status"]).lower() in ("ok", "ready", "healthy")
        return True
