"""Small helpers for talking to the core's control socket in tests."""
import asyncio
import json

from core.control import ControlServer


async def raw(path, request: bytes, *, read=True, eof=False):
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write(request)
    await writer.drain()
    if eof:
        writer.write_eof()
    data = b""
    if read:
        data = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    return data


def parse(data: bytes):
    head, _, body = data.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    return status, json.loads(body) if body else None


async def get(path, target="/status"):
    return parse(await raw(path, f"GET {target} HTTP/1.1\r\nHost: core\r\n\r\n".encode()))


def make_server(core, path, **kwargs):
    return ControlServer(path, service=core.service, runtime=core.runtime, **kwargs)
