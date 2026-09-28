import asyncio

from controller.events import LinkStateChanged, RemoteKeyed
from link.node_link import NodeLinkClient, parse_alinks


def test_parse_alinks_handles_real_captured_and_source_confirmed_formats():
    # "" and "0" both observed live (no links ever / links cleared).
    assert parse_alinks("") == {}
    assert parse_alinks("0") == {}
    # "1,1998TU" observed live after connecting node 1999 -> 1998.
    assert parse_alinks("1,1998TU") == {"1998": False}
    # Multi-node case per rpt_link.c's __mklinklist comma-joining -- not
    # observed live (would need a 3rd node) but directly follows the source.
    assert parse_alinks("2,1998TU,2000TK") == {"1998": False, "2000": True}


def test_node_link_client_translates_connect_keyup_and_disconnect():
    asyncio.run(_run())


async def _run() -> None:
    server = await asyncio.start_server(_fake_apprt_asterisk, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    async with server:
        client = NodeLinkClient(host, port, "admin", "secret", local_node_id="1999")
        await client.connect()
        events_iter = client.events()

        assert await events_iter.__anext__() == LinkStateChanged(node_id="1998", linked=True)
        assert await events_iter.__anext__() == RemoteKeyed(node_id="1998", keyed=True)
        assert await events_iter.__anext__() == RemoteKeyed(node_id="1998", keyed=False)
        assert await events_iter.__anext__() == LinkStateChanged(node_id="1998", linked=False)

        await client.close()


async def _fake_apprt_asterisk(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.write(b"Asterisk Call Manager/11.0.0\r\n")
    await writer.drain()

    login = await _read_block(reader)
    _write_block(writer, {"Response": "Success", "Message": "Authentication accepted", "ActionID": login["ActionID"]})
    await writer.drain()

    # connects already keyed (both happen in one event, as app_rpt's
    # rpt_update_links only fires once per link-set change)
    _write_block(writer, {"Event": "RPT_ALINKS", "Node": "1999", "EventValue": "1,1998TK"})
    await writer.drain()
    # unkeys
    _write_block(writer, {"Event": "RPT_ALINKS", "Node": "1999", "EventValue": "1,1998TU"})
    await writer.drain()
    # disconnects
    _write_block(writer, {"Event": "RPT_ALINKS", "Node": "1999", "EventValue": "0"})
    await writer.drain()

    writer.close()
    await writer.wait_closed()


def _write_block(writer: asyncio.StreamWriter, fields: dict[str, str]) -> None:
    for key, value in fields.items():
        writer.write(f"{key}: {value}\r\n".encode())
    writer.write(b"\r\n")


async def _read_block(reader: asyncio.StreamReader) -> dict[str, str]:
    message: dict[str, str] = {}
    while True:
        line = (await reader.readline()).decode().rstrip("\r\n")
        if line == "":
            return message
        key, _, value = line.partition(": ")
        message[key] = value
