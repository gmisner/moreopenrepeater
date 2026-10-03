"""CAT control through Hamlib's rigctld, which speaks to most radios.

Run `rigctld -m <model> -r /dev/ttyUSB0 -s <baud>` beside the controller and
point the remote base at it (`localhost:4532` by default). Commands use the
Extended Response Protocol (`+` prefix): every reply ends with `RPRT <n>`,
0 for success. Blocking; call it from a thread.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Optional

DEFAULT_PORT = 4532
TIMEOUT_SECONDS = 3.0
RIG_MODES = ("FM", "AM", "USB", "LSB")


class RigError(Exception):
    pass


def parse_address(address: str) -> tuple[str, int]:
    host, _, port = address.strip().rpartition(":")
    if not host:
        return address.strip(), DEFAULT_PORT
    return host.strip("[]"), int(port)


@dataclass(frozen=True)
class Tuning:
    mhz: float
    shift: str  # "simplex" | "plus" | "minus"
    offset_mhz: float  # ignored for simplex
    tone_hz: Optional[float]  # transmit CTCSS, None for off
    mode: str = "FM"


class Rigctld:
    def __init__(self, address: str, timeout: float = TIMEOUT_SECONDS) -> None:
        self._host, self._port = parse_address(address)
        self._timeout = timeout

    def _send(self, lines: list[str]) -> list[list[str]]:
        try:
            with socket.create_connection((self._host, self._port), timeout=self._timeout) as connection:
                stream = connection.makefile("rw", encoding="ascii", newline="\n")
                replies = []
                for line in lines:
                    stream.write(f"+{line}\n")
                    stream.flush()
                    reply = []
                    while True:
                        received = stream.readline()
                        if not received:
                            raise RigError("rigctld closed the connection")
                        received = received.strip()
                        reply.append(received)
                        if received.startswith("RPRT"):
                            break
                    replies.append(reply)
                return replies
        except OSError as error:
            raise RigError(f"couldn't reach rigctld at {self._host}:{self._port}: {error.strerror or error}") from None

    @staticmethod
    def _code(reply: list[str]) -> int:
        try:
            return int(reply[-1].split()[1])
        except (IndexError, ValueError):
            return -1

    def tune(self, tuning: Tuning) -> None:
        """Sets everything in one connection. The frequency and mode must
        work; the repeater shift and tone are skipped by radios without them."""
        tone = round(tuning.tone_hz * 10) if tuning.tone_hz else 0
        shift = {"plus": "+", "minus": "-"}.get(tuning.shift, "0")
        commands = [
            f"F {round(tuning.mhz * 1_000_000)}",
            f"M {tuning.mode} 0",
            f"R {shift}",
            f"O {round(tuning.offset_mhz * 1_000_000)}",
            f"C {tone}",
            f"U TONE {1 if tone else 0}",
        ]
        replies = self._send(commands)
        for command, reply in zip(commands[:2], replies[:2]):
            if self._code(reply) != 0:
                raise RigError(f"the radio refused {command.split()[0]} {command.split()[1]} (Hamlib error {self._code(reply)})")
