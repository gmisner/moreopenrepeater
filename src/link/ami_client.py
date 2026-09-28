"""Minimal asyncio Asterisk Manager Interface (AMI) client.

AMI is a simple line-based text protocol: the server sends a banner line on
connect, and both Actions (client -> server) and Responses/Events
(server -> client) are blocks of "Key: Value\\r\\n" lines terminated by a
blank line.

Asterisk interleaves unsolicited Events with action Responses on the same
connection at any time -- confirmed against a real Asterisk instance, which
pushed a "FullyBooted" Event immediately after login, ahead of the next
action's Response. So responses cannot be matched by read-order alone: every
action is tagged with a unique ActionID, and a single background reader task
demultiplexes incoming messages by ActionID, routing anything else (an
Event, or an unsolicited Response with no matching ActionID) to the event
queue that `events()` consumes.
"""
from __future__ import annotations

import asyncio
import itertools
from typing import AsyncIterator, Optional, Union

AMIValue = Union[str, list[str]]
AMIMessage = dict[str, AMIValue]


async def _read_message(reader: asyncio.StreamReader) -> Optional[AMIMessage]:
    """A Command action's response repeats the "Output" header once per line
    of console output (confirmed against a real Asterisk instance) -- a
    plain-dict assignment would silently keep only the last line, so a
    repeated key is collected into a list instead of overwriting.
    """
    message: AMIMessage = {}
    while True:
        line = await reader.readline()
        if not line:
            return None  # connection closed
        decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if decoded == "":
            if message:
                return message
            continue  # skip stray blank lines (e.g. before the banner)
        key, _, value = decoded.partition(": ")
        if key in message:
            existing = message[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                message[key] = [existing, value]
        else:
            message[key] = value


class AMIClient:
    def __init__(self, host: str, port: int, username: str, secret: str) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._secret = secret
        self._writer: Optional[asyncio.StreamWriter] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._action_ids = itertools.count(1)
        self._pending: dict[str, "asyncio.Future[AMIMessage]"] = {}
        self._events: "asyncio.Queue[Optional[AMIMessage]]" = asyncio.Queue()

    async def connect(self) -> None:
        reader, self._writer = await asyncio.open_connection(self._host, self._port)
        await reader.readline()  # banner, e.g. "Asterisk Call Manager/9.0.0"
        self._reader_task = asyncio.create_task(self._read_loop(reader))

        response = await self.send_action(
            {"Action": "Login", "Username": self._username, "Secret": self._secret}
        )
        if response.get("Response") != "Success":
            raise ConnectionError(f"AMI login failed: {response}")

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        while True:
            message = await _read_message(reader)
            if message is None:
                break
            action_id = message.get("ActionID")
            future = self._pending.pop(action_id, None) if action_id else None
            if future is not None and not future.done():
                future.set_result(message)
            else:
                await self._events.put(message)
        await self._events.put(None)  # signal end-of-stream to events()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("AMI connection closed before a response arrived"))
        self._pending.clear()

    async def send_action(self, fields: AMIMessage) -> AMIMessage:
        """Send an action and wait for its Response. A caller-chosen
        ActionID is kept, for matching later events that echo it (e.g. an
        async Originate's OriginateResponse)."""
        assert self._writer is not None
        action_id = str(fields.get("ActionID") or f"mor-{next(self._action_ids)}")
        fields = {**fields, "ActionID": action_id}
        for key, value in fields.items():
            if any(c in f"{key}{value}" for c in "\r\n"):
                raise ValueError(f"AMI header {key!r} contains a line break")

        future: "asyncio.Future[AMIMessage]" = asyncio.get_running_loop().create_future()
        self._pending[action_id] = future

        for key, value in fields.items():
            self._writer.write(f"{key}: {value}\r\n".encode())
        self._writer.write(b"\r\n")
        await self._writer.drain()

        return await future

    async def events(self) -> AsyncIterator[AMIMessage]:
        while True:
            message = await self._events.get()
            if message is None:
                return
            yield message

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()
